# monocle3 轨迹图桥接（sc_pseudotime engine="monocle3"，重启评估
# 探针两轮钉注见 测试总结第六十段；桥接模式同 slingshot_bridge.R）。
# argv: pca_csv umap_csv out_dir root_cell [expr_mtx genes_csv]
# 输入：pca_csv = cell,PC1..PCk；umap_csv = cell,UMAP1,UMAP2
# （Python 侧已对齐同一细胞顺序）；root_cell = 根细胞条码（Python
# 定根四模式必给 iroot，无自由推根分支）。
# 可选 expr_mtx/genes_csv（graph_top_n>0 时 Python 侧 mmwrite 导出
# cells x genes 稀疏 log 表达 + 基因清单）——真表达重建 cds 表达层
# 后 graph_test 基因级沿轨迹 Moran's I；缺省走 PCA 伪表达（graph_test
# 跳过，PC 级 Moran's I 无基因语义不导出）。
# 设计：PCA 当"表达矩阵"建 cds（免传稀疏计数大矩阵——learn_graph
# 只消费 reducedDims 不触表达层），PCA/UMAP 手动灌 reducedDims；
# cluster_cells(UMAP) 得 monocle3 自有分区 → learn_graph 主图 →
# order_cells(root_cells) 定向。
# 输出：out_dir/mono3_pt.csv（cell,pseudotime；断连分区 NA）、
#       out_dir/mono3_graph.csv（主图 MST 边折点坐标）、
#       out_dir/mono3_graphtest.csv（可选，按 q_value 升序全量统计）。
args <- commandArgs(trailingOnly = TRUE)
pca_csv <- args[1]
umap_csv <- args[2]
out_dir <- args[3]
root_cell <- args[4]
expr_mtx <- if (length(args) >= 5) args[5] else ""
genes_csv <- if (length(args) >= 6) args[6] else ""

suppressMessages(library(monocle3))
suppressMessages(library(Matrix))

pca <- read.csv(pca_csv, row.names = 1, check.names = FALSE)
ump <- read.csv(umap_csv, row.names = 1, check.names = FALSE)
cells <- intersect(rownames(pca), rownames(ump))
pca <- as.matrix(pca[cells, , drop = FALSE])
ump <- as.matrix(ump[cells, , drop = FALSE])
if (!root_cell %in% cells) {
  stop(sprintf("root_cell %s not in cells", root_cell))
}

use_real_expr <- nzchar(expr_mtx) && nzchar(genes_csv)
if (use_real_expr) {
  # Python mmwrite 写 cells x genes，readMM 读出同向，t() 转 genes x cells；
  # genes.csv 为无表头单列（read.csv 默认 header=TRUE 会吞掉首行）
  expr <- as(t(readMM(expr_mtx)), "CsparseMatrix")
  genes <- read.csv(genes_csv, header = FALSE,
                    stringsAsFactors = FALSE)[[1]]
  stopifnot(nrow(expr) == length(genes), ncol(expr) == length(cells))
  rownames(expr) <- genes
  colnames(expr) <- cells
} else {
  expr <- Matrix(t(pca), sparse = TRUE)
}
gene_md <- data.frame(row.names = rownames(expr),
                      gene_short_name = rownames(expr))
# pData 单列时 learn_graph→connect_tips 的 pd[rows, ] 子集会丢维度
# （monocle3 已知坑）——补一列 barcode 凑两列规避
cell_md <- data.frame(row.names = cells,
                      Size_Factor = rep(1.0, length(cells)),
                      cell_barcode = cells)
cds <- new_cell_data_set(expr, cell_metadata = cell_md,
                         gene_metadata = gene_md)
reducedDims(cds)[["PCA"]] <- pca
reducedDims(cds)[["UMAP"]] <- ump

cds <- cluster_cells(cds, reduction_method = "UMAP", verbose = FALSE)
cds <- learn_graph(cds, use_partition = TRUE, verbose = FALSE)
cds <- order_cells(cds, root_cells = root_cell)

pt <- pseudotime(cds, reduction_method = "UMAP")
write.csv(data.frame(cell = names(pt), pseudotime = unname(pt)),
          file.path(out_dir, "mono3_pt.csv"), row.names = FALSE)

# 主图 MST 折线导出（Python 侧画线，免引入 sf 绘图栈）。
# 1.4.27 结构实测（容器内 str() 探针）：
#   principal_graph(cds)[["UMAP"]] 本身即 igraph，节点名 Y_1..Y_n；
#   节点坐标在 principal_graph_aux[["UMAP"]]$dp_mst（2 行 x n 列，
#   行为 UMAP 两维、列对应 Y_i 节点），旧文档的 dp_mst_coords 已改名。
g <- principal_graph(cds)[["UMAP"]]
dp <- cds@principal_graph_aux[["UMAP"]]$dp_mst
el <- igraph::as_edgelist(g)
vidx <- function(nm) {
  m <- match(nm, colnames(dp))
  fallback <- as.integer(sub("^Y_", "", nm))
  ifelse(is.na(m), fallback, m)
}
a <- vidx(el[, 1])
b <- vidx(el[, 2])
edges <- data.frame(from = el[, 1], to = el[, 2],
                    x1 = dp[1, a], y1 = dp[2, a],
                    x2 = dp[1, b], y2 = dp[2, b])
write.csv(edges, file.path(out_dir, "mono3_graph.csv"), row.names = FALSE)

# graph_test（仅真表达路径）：沿主图邻接的基因级 Moran's I 自相关，
# q_value BH 校正；按 q_value 升序、Moran's I 降序全量导出（Python
# 侧切 top N）。cores 与容器 --cpus 4 默认对齐。
if (use_real_expr) {
  deg <- graph_test(cds, neighbor_graph = "principal_graph", cores = 4,
                    verbose = FALSE)
  deg <- deg[order(deg$q_value, -deg$morans_I), ]
  write.csv(data.frame(gene = rownames(deg), deg, check.names = FALSE),
            file.path(out_dir, "mono3_graphtest.csv"), row.names = FALSE)
}
cat("MONO3_DONE", sum(is.finite(pt)), "of", length(pt), "\n")
