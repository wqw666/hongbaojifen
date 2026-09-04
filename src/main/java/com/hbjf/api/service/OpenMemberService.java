package com.hbjf.api.service;

import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 会员开放接口服务（agent 侧群成员同步用）
 * - queryPointsBatch：批量查积分（≤500，缺行补 exists=false，保持入参顺序）
 * - registerBatch：批量注册（幂等：qq UNIQUE，重复返回 existed；不入事务，防 rollback-only）
 */
@Service
public class OpenMemberService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final int QUERY_BATCH_MAX = 500;
    private static final int REGISTER_BATCH_MAX = 1000;

    private final JdbcTemplate jdbc;

    public OpenMemberService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 批量查积分（qqs 保序去重后查询，最多 500 个） */
    public List<Map<String, Object>> queryPointsBatch(List<String> qqs) {
        if (qqs == null || qqs.isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "qqs不能为空");
        if (qqs.size() > QUERY_BATCH_MAX) throw new ApiException(ErrorCode.PARAM_INVALID, "一次最多查询 " + QUERY_BATCH_MAX + " 个QQ");

        List<Object> args = new ArrayList<>(qqs);
        String in = String.join(",", Collections.nCopies(qqs.size(), "?"));
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT qq, nickname, points, total_income, total_outcome, status FROM members WHERE qq IN (" + in + ")",
                new RowMapMapper(), args.toArray());

        Map<String, Map<String, Object>> byQq = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            byQq.put(String.valueOf(row.get("qq")), row);
        }
        List<Map<String, Object>> result = new ArrayList<>(qqs.size());
        for (String qq : qqs) {
            Map<String, Object> row = byQq.get(qq);
            if (row == null) {
                result.add(MapBuilder.of("qq", qq, "nickname", "", "points", 0,
                        "total_income", 0, "total_outcome", 0, "exists", false));
            } else {
                result.add(MapBuilder.of("qq", qq, "nickname", row.get("nickname"),
                        "points", row.get("points"), "total_income", row.get("total_income"),
                        "total_outcome", row.get("total_outcome"), "exists", true));
            }
        }
        return result;
    }

    /**
     * 批量注册会员（幂等）
     * 入参：members = [{qq, nickname?, group_id?}]；registrarQq = 注册人（管理员QQ，记录建档来源）
     * 已存在：existed+1；nickname/group_id 非空才覆盖（防空串冲库）；已存在行的注册人不覆盖
     * 非法 qq：skipped+1，不中断整批
     */
    public Map<String, Object> registerBatch(List<Map<String, Object>> members, String registrarQq) {
        if (members == null || members.isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "members不能为空");
        if (members.size() > REGISTER_BATCH_MAX) throw new ApiException(ErrorCode.PARAM_INVALID, "一次最多注册 " + REGISTER_BATCH_MAX + " 个会员");

        String now = LocalDateTime.now().format(FMT);
        String registrar = registrarQq == null ? "" : registrarQq.trim();
        int added = 0, existed = 0, skipped = 0;
        for (Map<String, Object> m : members) {
            String qq = m.get("qq") == null ? "" : String.valueOf(m.get("qq")).trim();
            String nickname = m.get("nickname") == null ? "" : String.valueOf(m.get("nickname"));
            String groupId = m.get("group_id") == null ? "" : String.valueOf(m.get("group_id"));
            if (!qq.matches("\\d{5,12}")) {
                skipped++;
                continue;
            }
            try {
                jdbc.update("INSERT INTO members (qq, nickname, group_id, status, registrar_qq, created_at, updated_at)"
                                + " VALUES (?,?,?,?,?,?,?)",
                        qq, nickname, groupId, "active", registrar, now, now);
                added++;
            } catch (org.springframework.dao.DuplicateKeyException e) {
                existed++;
                if (!nickname.isEmpty() || !groupId.isEmpty()) {
                    jdbc.update("UPDATE members SET nickname=COALESCE(NULLIF(?,''),nickname), group_id=COALESCE(NULLIF(?,''),group_id), updated_at=? WHERE qq=?",
                            nickname, groupId, now, qq);
                }
            }
        }
        return MapBuilder.of("total", members.size(), "added", added, "existed", existed, "skipped", skipped);
    }
}
