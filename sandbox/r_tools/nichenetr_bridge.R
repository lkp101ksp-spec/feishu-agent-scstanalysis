# NicheNet 配体活性优先级桥接（sc_nichenet，2026-09-18 八补记
# q2 探针终判落地；Browaeys et al. 2020 Nat Methods / nichenetr v2）。
# argv: in_dir species top_n min_expr
# 输入（in_dir 下，Python 侧备好）：
#   geneset.csv     —— 一列无表头（receiver 侧目标基因集）
#   expr_stats.csv  —— gene,sender_pct,receiver_pct 三列（Python 侧
#                     从 processed.h5ad X 按群掩码统计的表达比例；
#                     KB 级轻量桥接——矩阵本体不出 Python）
# 输出（in_dir 下）：
#   nn_ligand_activities.csv —— test_ligand/auroc/aupr/aupr_corrected/
#                     pearson/rank 全量降序（tibble 口径：配体名在
#                     test_ligand 列，八补记探针坑代订正 skill v1）
#   nn_ligand_target_links.csv —— top_n 配体×geneset 调控边
#   nn_meta.json     —— 计数元信息（Python 侧判 INVALID_INPUT 用）
# 先验 /opt/nichenet_prior/ 四件构建期烘焙（Zenodo 7074291，断网
# 可用）；lt 全零配体列必须剔除、background 须含配体自身（探针坑）。
suppressMessages(library(nichenetr))

args <- commandArgs(trailingOnly = TRUE)
in_dir <- args[1]
species <- args[2]
top_n <- if (length(args) >= 3 && nzchar(args[3])) as.integer(args[3]) else 20L
min_expr <- if (length(args) >= 4 && nzchar(args[4])) as.numeric(args[4]) else 0.05

prior <- "/opt/nichenet_prior"
lt_file <- if (species == "human") "ligand_target_matrix_nsga2r_final.rds" else
  "ligand_target_matrix_nsga2r_final_mouse.rds"
lr_file <- if (species == "human") "lr_network_human_21122021.rds" else
  "lr_network_mouse_21122021.rds"

lt <- readRDS(file.path(prior, lt_file))
lr <- readRDS(file.path(prior, lr_file))
lt <- lt[, colSums(lt) > 0, drop = FALSE]  # 全零配体列剔除（否则 aupr NA）

stats <- read.csv(file.path(in_dir, "expr_stats.csv"))
geneset <- readLines(file.path(in_dir, "geneset.csv"))

bg <- stats$gene[stats$receiver_pct >= min_expr]
sender_expressed <- stats$gene[stats$sender_pct >= min_expr]
pot <- intersect(intersect(unique(lr$from), colnames(lt)), sender_expressed)
# background 须含配体自身（探针坑：配体不在背景 → aupr_corrected NA）
bg <- union(bg, pot)

gs <- intersect(geneset, rownames(lt))

meta <- list(n_geneset_input = length(geneset), n_geneset_used = length(gs),
             n_background = length(bg), n_ligands_tested = 0L,
             n_sender_expressed = length(sender_expressed))

if (length(gs) >= 5 && length(pot) >= 3) {
  act <- nichenetr::predict_ligand_activities(
    geneset = gs, background_expressed_genes = bg,
    ligand_target_matrix = lt, potential_ligands = pot)
  act <- act[is.finite(act$aupr_corrected), ]
  act <- act[order(-act$aupr_corrected), ]
  act$rank <- seq_len(nrow(act))
  write.csv(act, file.path(in_dir, "nn_ligand_activities.csv"),
            row.names = FALSE)
  meta$n_ligands_tested <- nrow(act)
  if (nrow(act) > 0) {
    links <- nichenetr::get_weighted_ligand_target_links(
      ligand = head(act$test_ligand, top_n), geneset = gs,
      ligand_target_matrix = lt, n = 200)
    write.csv(links, file.path(in_dir, "nn_ligand_target_links.csv"),
              row.names = FALSE)
    meta$n_links <- nrow(links)
    meta$top_ligand <- act$test_ligand[1]
    meta$top_aupr <- act$aupr_corrected[1]
  }
}

# 手写 json（同 cytotrace2_bridge 口径——不引 jsonlite 依赖）
js <- sprintf(
  '{"n_geneset_input": %d, "n_geneset_used": %d, "n_background": %d, "n_sender_expressed": %d, "n_ligands_tested": %d%s%s}',
  meta$n_geneset_input, meta$n_geneset_used, meta$n_background,
  meta$n_sender_expressed, meta$n_ligands_tested,
  if (is.null(meta$top_ligand)) "" else
    sprintf(', "top_ligand": "%s", "top_aupr": %.4f',
            meta$top_ligand, meta$top_aupr),
  if (is.null(meta$n_links)) "" else sprintf(', "n_links": %d', meta$n_links))
cat(js, file = file.path(in_dir, "nn_meta.json"))
cat("NN_DONE", meta$n_ligands_tested, "ligands;", length(gs),
    "geneset used\n")
