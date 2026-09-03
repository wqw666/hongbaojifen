package com.hbjf.api.controller;

import com.hbjf.api.service.MemberService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 对外开放积分接口（供执行器 agent / 外部系统调用）
 * 需请求头 X-Api-Key（配置 app.open.api-key）
 * 契约：
 *   POST /api/open/points/up     {qq, points, reason, bizNo?}   上分
 *   POST /api/open/points/down   {qq, points, reason, bizNo?}   下分（余额不足拒绝）
 *   GET  /api/open/points/{qq}                                  查积分
 *   GET  /api/open/points/records?qq=&page=&size=               查流水
 */
@RestController
@RequestMapping("/api/open/points")
public class OpenPointController {

    private static final String OPERATOR = "open";

    private final MemberService memberService;

    public OpenPointController(MemberService memberService) {
        this.memberService = memberService;
    }

    /** 上分（增加积分） */
    @PostMapping("/up")
    public ResponseEntity<Map<String, Object>> up(@RequestBody Map<String, Object> body) {
        long points = parsePoints(body.get("points"));
        if (points <= 0) return err("上分数量必须大于0");
        Map<String, Object> result = memberService.adjustPoints(
                str(body.get("qq")), points, str(body.get("reason")), OPERATOR, str(body.get("bizNo")));
        return ok(result);
    }

    /** 下分（减少积分） */
    @PostMapping("/down")
    public ResponseEntity<Map<String, Object>> down(@RequestBody Map<String, Object> body) {
        long points = parsePoints(body.get("points"));
        if (points <= 0) return err("下分数量必须大于0");
        Map<String, Object> result = memberService.adjustPoints(
                str(body.get("qq")), -points, str(body.get("reason")), OPERATOR, str(body.get("bizNo")));
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

    /** 查询积分增减记录 */
    @GetMapping("/records")
    public ResponseEntity<Map<String, Object>> records(@RequestParam String qq,
                                                       @RequestParam(defaultValue = "1") int page,
                                                       @RequestParam(defaultValue = "20") int size) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", memberService.records(qq, null, page, size)));
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
