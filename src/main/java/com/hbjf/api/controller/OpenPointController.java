package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.service.MemberService;
import com.hbjf.api.service.OpenMemberService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/**
 * 对外开放积分接口（供执行器 agent / 外部系统调用）
 * 需请求头 X-Api-Key（配置 app.open.api-key）
 * 契约：
 *   POST /api/open/points/up     {qq, points, reason, bizNo?, executor_token?}   上分
 *   POST /api/open/points/down   {qq, points, reason, bizNo?, executor_token?}   下分（余额不足拒绝）
 *   GET  /api/open/points/{qq}                                  查积分
 *   GET  /api/open/points/batch?qqs=10001,10002,...             批量查积分（≤500，逗号分隔）
 *   GET  /api/open/points/records?qq=&page=&size=               查流水
 * executor_token 可选项：存在则走执行器手工上/下分门禁（执行器未被封禁、绑定操作员在线、且
 * 操作员 can_manual_points=allowed 才放行；操作人记执行器名）。不带 token 保持历史开放语义。
 */
@RestController
@RequestMapping("/api/open/points")
public class OpenPointController {

    private static final String OPERATOR = "open";
    private static final int QUERY_BATCH_MAX = 500;

    private final MemberService memberService;
    private final OpenMemberService openMemberService;
    private final ExecutorService executorService;

    public OpenPointController(MemberService memberService, OpenMemberService openMemberService,
                               ExecutorService executorService) {
        this.memberService = memberService;
        this.openMemberService = openMemberService;
        this.executorService = executorService;
    }

    /** 上分（增加积分；带 executor_token 时经操作员权限门禁） */
    @PostMapping("/up")
    public ResponseEntity<Map<String, Object>> up(@RequestBody Map<String, Object> body) {
        long points = parsePoints(body.get("points"));
        if (points <= 0) return err("上分数量必须大于0");
        Map<String, Object> result = memberService.adjustPoints(
                str(body.get("qq")), points, str(body.get("reason")),
                operator(body), str(body.get("bizNo")));
        return ok(result);
    }

    /** 下分（减少积分；带 executor_token 时经操作员权限门禁） */
    @PostMapping("/down")
    public ResponseEntity<Map<String, Object>> down(@RequestBody Map<String, Object> body) {
        long points = parsePoints(body.get("points"));
        if (points <= 0) return err("下分数量必须大于0");
        Map<String, Object> result = memberService.adjustPoints(
                str(body.get("qq")), -points, str(body.get("reason")),
                operator(body), str(body.get("bizNo")));
        return ok(result);
    }

    /** 查询某会员积分 */
    @GetMapping("/{qq}")
    public ResponseEntity<Map<String, Object>> query(@PathVariable String qq) {
        Map<String, Object> m = memberService.findByQq(qq);
        if (m == null) {
            return ResponseEntity.ok(MapBuilder.of("code", 0, "data", MapBuilder.of(
                    "qq", qq, "points", 0, "total_income", 0, "total_outcome", 0, "exists", false)));
        }
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", MapBuilder.of(
                "qq", qq,
                "points", m.get("points"),
                "total_income", m.get("total_income"),
                "total_outcome", m.get("total_outcome"),
                "nickname", m.get("nickname"),
                "exists", true)));
    }

    /** 批量查积分（逗号分隔 qqs，去重保序，最多 500 个；缺失返回 exists=false） */
    @GetMapping("/batch")
    public ResponseEntity<Map<String, Object>> batchQuery(@RequestParam String qqs) {
        if (qqs == null || qqs.trim().isEmpty()) return err("qqs不能为空");
        List<String> list = new ArrayList<>(new LinkedHashSet<>());
        for (String part : qqs.split(",")) {
            String q = part.trim();
            if (!q.isEmpty()) list.add(q);
        }
        if (list.isEmpty()) return err("qqs不能为空");
        if (list.size() > QUERY_BATCH_MAX) return err("一次最多查询 " + QUERY_BATCH_MAX + " 个QQ");
        return ok(MapBuilder.of("list", openMemberService.queryPointsBatch(list), "total", list.size()));
    }

    /** 查询积分增减记录 */
    @GetMapping("/records")
    public ResponseEntity<Map<String, Object>> records(@RequestParam String qq,
                                                       @RequestParam(defaultValue = "1") int page,
                                                       @RequestParam(defaultValue = "20") int size) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", memberService.records(qq, null, page, size)));
    }

    /**
     * 操作人解析：带 executor_token → requireOperable("manual") 门禁，操作人=执行器名
     * 不带 → 历史开放语义，操作人=open
     */
    private String operator(Map<String, Object> body) {
        String token = str(body.get("executor_token"));
        if (token.isEmpty()) return OPERATOR;
        Map<String, Object> ex = executorService.requireOperable(token, "manual");
        return "executor:" + ex.get("name");
    }

    private ResponseEntity<Map<String, Object>> ok(Map<String, Object> result) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", result));
    }

    private ResponseEntity<Map<String, Object>> err(String message) {
        return ResponseEntity.badRequest().body(MapBuilder.of("code", 40001, "message", message));
    }

    private long parsePoints(Object v) {
        try {
            return v == null ? 0 : Long.parseLong(String.valueOf(v));
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    private String str(Object v) {
        return v == null ? "" : String.valueOf(v);
    }
}
