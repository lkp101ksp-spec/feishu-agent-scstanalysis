"""数据画像提取单测（Phase 42）：h5py 直读 h5ad，dense/CSR 双编码。

构造 tiny h5ad（anndata 0.2 编码约定：X dense array 或 csr group，
var 为 dataframe 组含 _index）验证统计正确性与容错。
"""
import h5py
import numpy as np

from orchestrator.tools.bio.dataset_profile import (
    build_profile_context,
    extract_dataset_refs,
    profile_dataset,
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
