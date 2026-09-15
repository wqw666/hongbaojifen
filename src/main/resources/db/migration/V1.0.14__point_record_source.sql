-- 积分来源分离：后台手动 / 群内审批 / 玩法结算
-- 在此之前「手动改分」与「玩法结算改分」只能靠 reason 文案约定区分（reason LIKE '玩法:%'，见 ReportService），
-- 会员流水里两者混在一起、也没法按来源统计流水。本迁移把它落成独立字段。
ALTER TABLE point_records
    ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'manual'
        COMMENT '来源: manual 后台手动 / approve 群内审批 / game 玩法结算' AFTER flow_amount;

-- 历史回填：玩法结算（agent 恒写 reason='玩法:{玩法名} 结算'）
--           群内审批（agent 审批通过走 open 接口，biz_no='approve:...'）
--           其余（后台手动、open 直调）= manual
UPDATE point_records SET source = CASE
    WHEN reason LIKE '玩法:%' THEN 'game'
    WHEN biz_no LIKE 'approve:%' THEN 'approve'
    ELSE 'manual' END;

-- 流水额口径统一：手动/审批也按全额记流水（与 V1.0.13 回填 flow_amount=delta 一致）；
-- V1.0.13 之后写入的手动流水 flow_amount 落了默认 0，这里补齐
UPDATE point_records SET flow_amount = delta
    WHERE source IN ('manual','approve') AND flow_amount = 0 AND delta <> 0;

-- 会员列表按「会员 × 来源」聚合、流水抽屉按来源过滤
CREATE INDEX idx_records_member_source ON point_records (member_id, source);
