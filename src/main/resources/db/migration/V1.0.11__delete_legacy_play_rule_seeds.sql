-- ========== 红包积分管理系统 V1.0.11：删除旧玩法种子元数据（复合玩法唯一化收敛） ==========
-- 复合玩法唯一化（评审 M1 处置）：V1.0.2（rule_add1/rule_add2）与 V1.0.7（rule_redpacket）
-- 迁移在全新数据库路径下仍会插入三行 active 旧玩法元数据，而对应 classpath 种子文件
-- （seed_rules/rule_add1.py|rule_add2.py|rule_redpacket.py）已随收敛删除——
-- 新环境会出现 3 行指向缺失文件的脏玩法（agent 下载 404、管理列表脏行）。
-- 本迁移清掉全部非「复合玩法」行：
--   · 新库自愈：迁移阶段该表只有 V1.0.2/V1.0.7 插入的三行旧种子（无其它迁移写入），
--     删除后由 PlayRuleService.ensureSeedRules() 在应用启动 @PostConstruct 幂等补齐
--     唯一玩法「复合玩法」元数据行；
--   · 老库自愈：已收敛库删除 0 行（幂等无害）；残留的旧种子/历史玩法行一并清除。
-- 磁盘文件不在本迁移处理（SQL 写不了磁盘）：收敛库已随管理流程清理，新库不会落盘旧种子。
-- file_name 以种子前缀 seed_rule_ 命名，与复合玩法 seed_rule_fuhe.py 无交集。

DELETE FROM play_rule_files WHERE name <> '复合玩法';
