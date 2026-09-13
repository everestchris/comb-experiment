# Fetching the connectome

comb never invents connectome weights. Without a graph, `roam_hive.py`
still roams and still dances - BRAIN OFFLINE, waggle computed straight from
the URL hash (dance.py) - this download only turns the antennal-lobe and
mushroom-body simulation on.

```bash
mkdir -p data build

# Download these three feathers from storage.googleapis.com/flyem-male-cns
# (FlyEM male CNS v1.0, CC-BY, no account needed) and save them under the
# plain names build_graph.py expects:
BASE=https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome
curl -L -o data/connectome-weights.feather     "$BASE/connectome-weights-male-cns-v1.0-minconf-0.5.feather"
curl -L -o data/body-neurotransmitters.feather "$BASE/body-neurotransmitters-male-cns-v1.0.feather"
curl -L -o data/body-annotations.feather       "$BASE/body-annotations-male-cns-v1.0-minconf-0.5.feather"

python build_graph.py       # writes build/graph.npz (~1.1 GB download, one time)
FLY_ALLOW_BROWSER=1 python roam_hive.py
```

## Where roam_hive.py looks

`hive.py`'s `resolve_graph_path()` checks, in order:

1. `./build/graph.npz` - this repo's own build directory (the path above writes here)
2. `../flycoinrh/build/graph.npz` - a sibling clone of fruitflydev/flycoinrh.
   If you already built the fly's connectome there, point a sibling `comb/`
   checkout at it instead of downloading the ~1.1 GB feathers again.
3. `$FLY_GRAPH` - an explicit path override, if you keep the graph somewhere else

If none of the three exist, the hive prints the three feather names and this
bucket path, then keeps roaming with `brain_online=False`. It never crashes
and never fakes a graph to get BRAIN ON to show - that flag only ever turns
on after `FlyBrain()` has actually loaded one.
