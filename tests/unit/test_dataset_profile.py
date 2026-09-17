"""数据画像提取单测（Phase 42）：h5py 直读 h5ad，dense/CSR 双编码。

构造 tiny h5ad（anndata 0.2 编码约定：X dense array 或 csr group，
var 为 dataframe 组含 _index）验证统计正确性与容错。
执行面统一轮新增：物种猜测（基因符号风格）/obs 列/_profiles 记忆库
持久化与 resolve_species 解析链。
"""
import h5py
import numpy as np

from orchestrator.tools.bio.dataset_profile import (
    build_profile_context,
    cached_species_guess,
    detect_symbol_style,
    extract_dataset_refs,
    profile_dataset,
    resolve_species,
)


def _write_dense_h5ad(path, x: np.ndarray, gene_names, *, mt_col=None):
    """写 dense X + var dataframe 的 tiny h5ad。"""
    with h5py.File(path, "w") as f:
        f.create_dataset("X", data=x)
        var = f.create_group("var")
        var.attrs["encoding-type"] = "dataframe"
        var.attrs["encoding-version"] = "0.2.0"
        var.attrs["_index"] = "_index"
        var.create_dataset("_index", data=[g.encode() for g in gene_names])
        if mt_col is not None:
            var.create_dataset("mt", data=np.asarray(mt_col, dtype=bool))


def _write_csr_h5ad(path, x_dense: np.ndarray, gene_names):
    """把 dense 矩阵转 CSR（anndata csr_matrix 编码）写 h5ad。"""
    data, indices, indptr = [], [], [0]
    for row in x_dense:
        nz = np.nonzero(row)[0]
        data.extend(row[nz].tolist())
        indices.extend(nz.tolist())
        indptr.append(len(data))
    with h5py.File(path, "w") as f:
        x = f.create_group("X")
        x.attrs["encoding-type"] = "csr_matrix"
        x.attrs["encoding-version"] = "0.1.0"
        x.attrs["shape"] = x_dense.shape
        x.create_dataset("data", data=np.asarray(data, dtype=np.float64))
        x.create_dataset("indices", data=np.asarray(indices, dtype=np.int64))
        x.create_dataset("indptr", data=np.asarray(indptr, dtype=np.int64))
        var = f.create_group("var")
        var.attrs["_index"] = "_index"
        var.create_dataset("_index", data=[g.encode() for g in gene_names])


# 3 细胞 × 4 基因：每细胞非零数 2/3/1；g1 为线粒体基因
_X = np.array([
    [5, 0, 3, 0],
    [0, 2, 1, 4],
    [0, 0, 7, 0],
], dtype=np.float64)
_GENES = ["MT-ND1", "G2", "G3", "G4"]


class TestExtractRefs:
    def test_extracts_12hex_dedup_ordered(self):
        text = "对 f1e89bf88edc 和 50165a559c91 分析，f1e89bf88edc 重复"
        assert extract_dataset_refs(text) == ["f1e89bf88edc", "50165a559c91"]

    def test_ignores_non_hex_and_wrong_length(self):
        assert extract_dataset_refs("hello world 12345 zzzzzzzzzzzz") == []
        # 13 位不匹配 \b 边界内的 12 hex（前后缀字符非 hex 才算独立词）
        assert extract_dataset_refs("abc") == []


class TestProfileDense:
    def test_dense_stats_and_mt_from_prefix(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _GENES)
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        assert p["n_cells"] == 3 and p["n_genes"] == 4
        g = p["genes_per_cell"]
        assert g["median"] == 2.0 and g["max"] == 3   # 非零数 2/3/1
        # mt：细胞1 = 5/8=62.5%，细胞2 = 0，细胞3 = 0
        assert "mt_pct" in p and p["mt_pct"]["median"] == 0.0
        assert p["source"] == "raw.h5ad"

    def test_mt_boolean_column_preferred(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        # var['mt'] 标记 G3（非 MT- 前缀）→ 优先于前缀派生
        _write_dense_h5ad(ds / "raw.h5ad", _X, _GENES,
                          mt_col=[False, False, True, False])
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        # 细胞1 mt = 3/8=37.5%，细胞2 = 1/7≈14.3%，细胞3 = 100%
        # → 中位数 37.5
        assert abs(p["mt_pct"]["median"] - 37.5) < 1e-6

    def test_read_chain_prefers_raw(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _GENES)
        _write_dense_h5ad(ds / "processed.h5ad", _X[:2], _GENES)
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        assert p["source"] == "raw.h5ad" and p["n_cells"] == 3


class TestProfileCSR:
    def test_csr_stats_match_dense(self, tmp_path):
        ds = tmp_path / "bbbbbbbbbbbb"
        ds.mkdir()
        _write_csr_h5ad(ds / "raw.h5ad", _X, _GENES)
        p = profile_dataset(str(tmp_path), "bbbbbbbbbbbb")
        assert p["n_cells"] == 3 and p["n_genes"] == 4
        assert p["genes_per_cell"]["median"] == 2.0
        # CSR mt 分子分母：细胞1 = 5/8=62.5%，细胞2/3 = 0
        # numpy p95 线性插值：sorted[0,0,62.5] idx=1.9 → 0.9*62.5=56.25
        assert abs(p["mt_pct"]["median"] - 0.0) < 1e-9
        assert abs(p["mt_pct"]["p95"] - 56.25) < 1e-6


class TestFallbacks:
    def test_missing_dir_returns_none(self, tmp_path):
        assert profile_dataset(str(tmp_path), "cccccccccccc") is None

    def test_empty_workspace_root_returns_none(self):
        assert profile_dataset("", "aaaaaaaaaaaa") is None

    def test_dir_without_h5ad_returns_none(self, tmp_path):
        (tmp_path / "aaaaaaaaaaaa").mkdir()
        assert profile_dataset(str(tmp_path), "aaaaaaaaaaaa") is None

    def test_corrupt_file_returns_none(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        (ds / "raw.h5ad").write_bytes(b"not hdf5")
        assert profile_dataset(str(tmp_path), "aaaaaaaaaaaa") is None


class TestBuildContext:
    def test_context_contains_stats_and_guidance(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _GENES)
        ctx = build_profile_context("对 aaaaaaaaaaaa 做质控", str(tmp_path))
        assert "数据画像" in ctx
        assert "3 细胞 × 4 基因" in ctx
        assert "median=2" in ctx
        assert "min_genes" in ctx          # 阈值选择指引
        assert "严禁套用默认值" in ctx

    def test_no_ref_returns_empty(self, tmp_path):
        assert build_profile_context("随便聊聊", str(tmp_path)) == ""

    def test_empty_workspace_root_returns_empty(self):
        assert build_profile_context("对 aaaaaaaaaaaa 做质控", "") == ""

    def test_unknown_ref_returns_empty(self, tmp_path):
        assert build_profile_context("对 dddddddddddd 做质控",
                                     str(tmp_path)) == ""


# === 执行面统一轮：物种猜测 + 记忆库 ===

_MOUSE_GENES = ["Xkr4", "Gm1992", "Rp1", "Sox17", "Mrpl15", "Lypla1"]
_HUMAN_GENES = ["XKR4", "SOX17", "TGFB1", "CD3D", "MS4A1", "B2M"]


class TestSymbolStyle:
    def test_mouse_title_case(self):
        assert detect_symbol_style(_MOUSE_GENES + ["H2-K1"]) == "title"

    def test_human_upper(self):
        assert detect_symbol_style(_HUMAN_GENES) == "upper"

    def test_ensembl_ids(self):
        assert detect_symbol_style(
            ["ENSG00000141510", "ENSMUSG00000059552", "ENSG00000223972"]
        ) == "ensembl"

    def test_mixed_below_threshold(self):
        assert detect_symbol_style(["Xkr4", "TGFB1"]) == "mixed"


class TestSpeciesGuessPersist:
    def test_profile_detects_mouse_and_persists(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _MOUSE_GENES)
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        assert p["gene_style"] == "title"
        assert p["species_guess"] == "mouse"
        # 记忆库落盘（下划线前缀不被 GC hex 正则命中）
        cache = tmp_path / "_profiles" / "aaaaaaaaaaaa.json"
        assert cache.is_file()
        assert cached_species_guess(str(tmp_path), "aaaaaaaaaaaa") == "mouse"

    def test_profile_detects_human(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _HUMAN_GENES)
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        assert p["species_guess"] == "human"

    def test_obs_columns_collected(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _HUMAN_GENES)
        with h5py.File(ds / "raw.h5ad", "a") as f:
            obs = f.create_group("obs")
            obs.attrs["encoding-type"] = "dataframe"
            obs.attrs["_index"] = "_index"
            obs.create_dataset("_index", data=[b"c1", b"c2", b"c3"])
            grp = obs.create_group("leiden")  # categorical → Group
            grp.attrs["encoding-type"] = "categorical"
            obs.create_dataset("n_genes", data=np.array([1, 2, 3]))
        p = profile_dataset(str(tmp_path), "aaaaaaaaaaaa")
        assert p["obs_cols"]["categorical"] == ["leiden"]
        assert "n_genes" in p["obs_cols"]["other"]

    def test_cached_guess_no_dataset_returns_none(self, tmp_path):
        assert cached_species_guess(str(tmp_path), "eeeeeeeeeeee") is None

    def test_cached_guess_bad_root_types_return_none(self):
        # MagicMock 属性等非 str 入参不抛、返回 None（单测 handler 兜底）
        assert cached_species_guess(None, "aaaaaaaaaaaa") is None
        assert cached_species_guess("", "aaaaaaaaaaaa") is None


class TestResolveSpecies:
    def test_explicit_passthrough(self, tmp_path):
        assert resolve_species(str(tmp_path), "aaaaaaaaaaaa", "mouse") == "mouse"
        assert resolve_species(str(tmp_path), "aaaaaaaaaaaa", "human") == "human"

    def test_empty_resolves_from_memory(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _MOUSE_GENES)
        # 首次调用即触发 profile 现算+落盘，二次命中缓存
        assert resolve_species(str(tmp_path), "aaaaaaaaaaaa", "") == "mouse"
        assert resolve_species(str(tmp_path), "aaaaaaaaaaaa", "") == "mouse"

    def test_empty_no_memory_falls_back_human(self, tmp_path):
        assert resolve_species(str(tmp_path), "eeeeeeeeeeee", "") == "human"

    def test_invalid_root_falls_back_human(self):
        assert resolve_species(None, "aaaaaaaaaaaa", "") == "human"


class TestContextSpeciesLine:
    def test_context_mentions_species_and_obs_cols(self, tmp_path):
        ds = tmp_path / "aaaaaaaaaaaa"
        ds.mkdir()
        _write_dense_h5ad(ds / "raw.h5ad", _X, _MOUSE_GENES)
        with h5py.File(ds / "raw.h5ad", "a") as f:
            obs = f.create_group("obs")
            obs.attrs["_index"] = "_index"
            obs.create_dataset("_index", data=[b"c1", b"c2", b"c3"])
            grp = obs.create_group("leiden")
            grp.attrs["encoding-type"] = "categorical"
        ctx = build_profile_context("对 aaaaaaaaaaaa 做通讯", str(tmp_path))
        assert "物种应为 mouse" in ctx
        assert "可用分组列：leiden" in ctx
        assert "species" in ctx  # 物种指引句
