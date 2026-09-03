package com.hbjf.api.controller;

import com.hbjf.api.service.MemberService;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 会员管理（管理员）
 */
@RestController
@RequestMapping("/api/admin/members")
public class MemberController {

    private final MemberService memberService;
    private final OperationLogService operationLogService;

    public MemberController(MemberService memberService, OperationLogService operationLogService) {
        this.memberService = memberService;
        this.operationLogService = operationLogService;
    }

    /** 列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword,
                                                    @RequestParam(required = false) String status) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", memberService.list(keyword, status)));
    }

    /** 统计 */
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> stats() {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", memberService.stats()));
    }

    /** 新增（手动建档） */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        memberService.create(body.get("qq"), body.get("nickname"), body.get("group_id"), body.get("note"));
        operationLogService.log(operator(auth), "新增会员", body.get("qq"), "手动建档", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增会员"));
    }

    /** 更新 */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                      Authentication auth, HttpServletRequest request) {
        memberService.update(id, body.get("nickname"), body.get("group_id"), body.get("note"), body.get("status"));
        operationLogService.log(operator(auth), "更新会员", String.valueOf(id), "昵称/备注/状态修改", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 删除（有流水则拒绝） */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        memberService.delete(id);
        operationLogService.log(operator(auth), "删除会员", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }

    /** 手动上下分：{delta, reason} */
    @PostMapping("/{id}/points")
    public ResponseEntity<Map<String, Object>> adjust(@PathVariable Long id, @RequestBody Map<String, Object> body,
                                                      Authentication auth, HttpServletRequest request) {
        Map<String, Object> member = memberService.findById(id);
        if (member == null) {
            throw new com.hbjf.api.exception.ApiException(com.hbjf.api.exception.ErrorCode.NOT_FOUND, "会员不存在");
        }
        String qq = String.valueOf(member.get("qq"));
        long delta = body.get("delta") == null ? 0 : Long.parseLong(String.valueOf(body.get("delta")));
        String reason = body.get("reason") == null ? "" : String.valueOf(body.get("reason"));
        Map<String, Object> result = memberService.adjustPoints(qq, delta, reason, operator(auth), null);
        operationLogService.log(operator(auth), delta > 0 ? "上分" : "下分", qq,
                (delta > 0 ? "+" : "") + delta + "，" + reason, clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", delta > 0 ? "上分成功" : "下分成功", "data", result));
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
