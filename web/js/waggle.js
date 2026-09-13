// web/js/waggle.js — dance playback on the 3D bee.
//
// Dancer (w0): consumes the `dancer` object from a /ws tick ({angle,
// duration, vigor, dancing}) and turns it into motion - a straight waggle
// run along `angle` with a ~15Hz yaw jitter (amplitude set by vigor) for
// the first part of the bout, then a silent return loop arcing back to the
// start on alternating sides each bout (the closest a stylised figure-eight
// gets without tracing a literal figure-eight path). No cartoon bounce.
//
// Attendants (w1-w7): idle motion that never depends on the backend tick -
// it runs identically whether the brain is online or offline, driven only
// by frame time (performance.now()) and each attendant's own phase offset
// (id*0.7), so the semicircle never reads as static geometry or moves in
// lockstep. Periodically, one attendant "recruits": a brief 0.6s walk along
// the dancer's current heading with a faint trailing streak, then back.
//
// Classic script on purpose, not an ES module: opening index.html as a
// plain file:// document (no server) is a hard requirement (see bee.js),
// and Chrome refuses to resolve `import` statements between local files
// under file:// even though a remote https import (three.js from a CDN)
// works fine there. A local module-to-module import chain is exactly what
// left the page dead on file:// before this fix, so every local script in
// web/js/ is plain global-scope JS, wired together via window.Comb.

(function () {
  const JITTER_HZ = 15;
  const DEG = Math.PI / 180;

  class Waggle {
    constructor(dancerGroup, attendantGroups) {
      this.dancerGroup = dancerGroup;
      this.attendantGroups = attendantGroups;
      this.state = { angle: 0, duration: 0, vigor: 0, dancing: false };
      this.phaseStart = performance.now();
      this._angle = 0;
      this._side = 1;
    }

    setDancerState(next) {
      const wasDancing = this.state.dancing;
      this.state = { ...this.state, ...next };
      if (this.state.dancing && !wasDancing) {
        this.phaseStart = performance.now();
        this._side = -this._side;   // alternate sides each bout, figure-eight style
      }
    }

    update(nowMs) {
      const t = nowMs / 1000;
      this._updateDancer(nowMs, t);
      this._updateAttendants(t);
    }

    _updateDancer(nowMs, t) {
      const { angle, dancing, vigor, duration } = this.state;

      // Ease the body toward the target heading rather than snapping to it -
      // a bee turning to start a run is still a turn, not a teleport.
      const target = dancing ? angle : this._angle;
      let delta = target - this._angle;
      while (delta > Math.PI) delta -= 2 * Math.PI;
      while (delta < -Math.PI) delta += 2 * Math.PI;
      this._angle += delta * 0.08;

      if (dancing) {
        const elapsed = (nowMs - this.phaseStart) / 1000;
        const frac = Math.min(1, elapsed / Math.max(0.05, duration));
        const runFrac = 0.55;          // straight waggle run vs. return loop
        const runDist = 0.09;
        const arcWidth = 0.05;

        if (frac < runFrac) {
          // The run: straight out along heading, waggling as it goes.
          const t2 = frac / runFrac;
          const fwd = t2 * runDist;
          this.dancerGroup.position.set(Math.sin(this._angle) * fwd, 0, Math.cos(this._angle) * fwd);
          const jitter = Math.sin(t * JITTER_HZ * Math.PI * 2) * 0.05 * (0.3 + vigor);
          this.dancerGroup.rotation.y = this._angle + jitter;
        } else {
          // The return loop: silent, arcs back to the start on this bout's
          // side, alternating side every bout - a figure-eight over time.
          const t3 = (frac - runFrac) / (1 - runFrac);
          const fwd = (1 - t3) * runDist;
          const lateral = this._side * Math.sin(t3 * Math.PI) * arcWidth;
          const px = Math.cos(this._angle), pz = -Math.sin(this._angle);
          this.dancerGroup.position.set(
            Math.sin(this._angle) * fwd + px * lateral, 0,
            Math.cos(this._angle) * fwd + pz * lateral,
          );
          this.dancerGroup.rotation.y = this._angle;
        }
      } else {
        // idle: a slow walk-in-place sway, no forward travel
        this.dancerGroup.position.x *= 0.9;
        this.dancerGroup.position.z *= 0.9;
        this.dancerGroup.position.y = Math.sin(t * 0.6) * 0.01;
        this.dancerGroup.rotation.y = this._angle + Math.sin(t * 0.35) * 0.06;
      }
    }

    _updateAttendants(t) {
      const dancerPos = this.dancerGroup.position;
      const RECRUIT_CYCLE = 5.2;   // seconds between one attendant's recruit walks
      const RECRUIT_DUR = 0.6;
      const WALK_R = 0.04;

      this.attendantGroups.forEach((g) => {
        if (!g.visible) return;
        const phase = g.userData.phase || 0;
        const rest = g.userData.rest || { x: g.position.x, y: 0, z: g.position.z };

        // face the dancer, before the idle yaw wobble is layered on top
        const faceAngle = Math.atan2(dancerPos.x - rest.x, dancerPos.z - rest.z);
        const yawWobble = Math.sin(t * 0.35 * Math.PI * 2 + phase) * 6 * DEG;

        // recruit: a brief directed walk along the dance heading, once per
        // cycle, offset per-attendant so they don't all go at once
        const cyclePos = (t + phase * 1.9) % RECRUIT_CYCLE;
        const recruiting = cyclePos < RECRUIT_DUR;

        if (recruiting) {
          const walkT = cyclePos / RECRUIT_DUR;
          const out = Math.sin(walkT * Math.PI);          // 0 -> 1 -> 0
          const dist = 0.22 * out;
          const heading = this.state.angle;
          const x = rest.x + Math.sin(heading) * dist;
          const z = rest.z + Math.cos(heading) * dist;
          g.position.set(x, rest.y, z);
          g.rotation.y = heading;

          const streak = g.userData.streak;
          if (streak) {
            const pos = streak.geometry.attributes.position;
            pos.setXYZ(0, rest.x, rest.y, rest.z);
            pos.setXYZ(1, x, rest.y, z);
            pos.needsUpdate = true;
            streak.visible = true;
            streak.material.opacity = 0.35 * out;
          }
        } else {
          // idle: small radius walk + z(vertical) bob, on top of facing
          // the dancer with a slow yaw wobble
          const wx = rest.x + Math.cos(t * 0.15 + phase) * WALK_R;
          const wz = rest.z + Math.sin(t * 0.11 + phase * 1.3) * WALK_R;
          const bob = Math.sin(t * 0.8 * Math.PI * 2 + phase) * 0.012;
          g.position.set(wx, rest.y + bob, wz);
          g.rotation.y = faceAngle + yawWobble;

          const streak = g.userData.streak;
          if (streak) streak.visible = false;
        }

        // antenna wiggle, independent of body yaw - see bee.js's
        // buildPointsSplit(), which pivots this sub-mesh at the antenna base
        const antenna = g.userData.antenna;
        if (antenna) {
          antenna.rotation.y = Math.sin(t * 2 * Math.PI * 2 + phase) * 8 * DEG;
        }
      });
    }
  }

  window.Comb = window.Comb || {};
  window.Comb.Waggle = Waggle;
})();
