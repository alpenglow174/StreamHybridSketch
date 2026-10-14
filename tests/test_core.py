import numpy as np

from fedsketch import dp
from fedsketch.data import decode_word, encode_word
from fedsketch.iblt import iblt_decode, iblt_encode
from fedsketch.protocol import EdgeLogic, VariantConfig, VariantServer
from fedsketch.secagg import SecAggParty, unmask_sum
from fedsketch.sketches import cs_encode, cs_query, fold


def test_secagg_masks_cancel():
    parties = [SecAggParty(i) for i in range(5)]
    pks = {p.id: p.public_key() for p in parties}
    for p in parties:
        p.set_peers(pks)
    rng = np.random.default_rng(0)
    vecs = [rng.integers(-10**12, 10**12, 1000) for _ in parties]
    masked = [p.mask(v, round_id=3) for p, v in zip(parties, vecs)]
    assert not np.array_equal(masked[0].view(np.int64), vecs[0])
    assert np.array_equal(unmask_sum(masked), np.sum(vecs, axis=0))


def test_fold_is_exact():
    keys = np.random.default_rng(1).integers(1, 10**6, 5000)
    big = cs_encode(keys, None, 4, 1024, 11, 22)
    small = cs_encode(keys, None, 4, 256, 11, 22)
    assert np.array_equal(fold(big, 256), small)


def test_countsketch_heavy_item():
    keys = np.concatenate([np.full(500, 42), np.arange(1000, 6000)])
    S = cs_encode(keys, None, 5, 512, 1, 2)
    assert abs(cs_query(S, [42], 1, 2)[0] - 500) < 30


def test_iblt_roundtrip_and_linearity():
    rng = np.random.default_rng(2)
    keys = rng.integers(1, 2**39, 300)
    a = iblt_encode(keys[:200], np.ones(200, np.int64), 3 * 256, seed=5)
    b = iblt_encode(keys[100:], np.ones(200, np.int64) * 2, 3 * 256, seed=5)
    out, ok = iblt_decode(a + b, seed=5)
    assert ok
    for k in keys[:100]:
        assert out[int(k)] == 1
    for k in keys[100:200]:
        assert out[int(k)] == 3


def test_dyadic_cover():
    for a in range(0, 40):
        for b in range(a, 70):
            cov = dp.dyadic_cover(a, b, 4)
            rounds = [r for nd in cov for r in range(dp.node_range(nd)[0], dp.node_range(nd)[1] + 1)]
            assert rounds == list(range(a, b + 1))


def test_word_codec():
    assert decode_word(encode_word("streaming")) == "streamin"


def test_tree_equals_exact_without_noise():
    """Without noise, tree and per-round variants must give identical cumulative
    estimates as a single sketch of the union with the same width/shared hash."""
    rng = np.random.default_rng(3)
    cfg = VariantConfig(name="t", mode="tree", width=256, horizon=16)
    dic = np.arange(1, 2001)
    srv = VariantServer(cfg, n_edges=3, dictionary=dic)
    edges = [EdgeLogic(cfg, e, 3) for e in range(3)]
    allkeys = []
    for t in range(11):
        plan = srv.plan(t)
        agg = 0
        for e in edges:
            ks = rng.choice(dic, 300, p=None)
            allkeys.append(ks)
            agg = agg + e.payload(plan, np.arange(len(ks)), ks)
        srv.ingest(plan, agg)
    allk = np.concatenate(allkeys)
    from fedsketch.protocol import bucket_seed, sign_seed
    ref = cs_query(cs_encode(allk, None, 5, 256, bucket_seed(cfg), sign_seed(cfg, 0)), dic,
                   bucket_seed(cfg), sign_seed(cfg, 0))
    assert np.allclose(srv.estimate(dic, "cum", 10), ref)
