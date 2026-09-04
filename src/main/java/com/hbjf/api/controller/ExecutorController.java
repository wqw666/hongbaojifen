package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 执行器管理（管理员）
 * 封禁参照主流会话吊销逻辑：banned_at 置位后该执行器全部请求立即被拒；
 * 支持「仅封禁」（解封后原 token 恢复）与「封禁并重置token」（旧凭据立即作废，防攻破续联）
 */
@RestController
@RequestMapping("/api/admin/executors")
public class ExecutorController {

    private final ExecutorService executorService;
    private final OperationLogService operationLogService;

    public ExecutorController(ExecutorService executorService, OperationLogService operationLogService) {
        this.executorService = executorService;
        this.operationLogService = operationLogService;
    }

    /** 列表：?keyword=&status=（status=banned 表示封禁筛选） */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword,
                                                    @RequestParam(required = false) String status) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", executorService.list(keyword, status)));
    }

    /** 新增（返回一次性 token） */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        Map<String, Object> result = executorService.create(body.get("name"),
                body.get("version"), body.get("host"));
        operationLogService.log(operator(auth), "新增执行器", body.get("name"), "已生成token", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增，请立即保存token（仅显示一次）", "data", result));
    }

    /** 更新基本信息（名称/版本；状态由心跳驱动，不能手工改成在线） */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                      Authentication auth, HttpServletRequest request) {
        executorService.update(id, body.get("name"), body.get("version"));
        operationLogService.log(operator(auth), "更新执行器", body.get("name"), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 删除 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        executorService.delete(id);
        operationLogService.log(operator(auth), "删除执行器", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }

    /** 重置 token */
    @PostMapping("/{id}/reset-token")
    public ResponseEntity<Map<String, Object>> resetToken(@PathVariable Long id, Authentication auth,
                                                          HttpServletRequest request) {
        String token = executorService.resetToken(id);
        operationLogService.log(operator(auth), "重置执行器token", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已重置，请立即保存新token（仅显示一次）", "token", token));
    }

    /** 封禁：{reason?, reset_token?} reset_token=true 时旧 token 立即作废（防攻破续联） */
    @PostMapping("/{id}/ban")
    public ResponseEntity<Map<String, Object>> ban(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                   Authentication auth, HttpServletRequest request) {
        boolean reset = Boolean.parseBoolean(body.get("reset_token"));
        Map<String, Object> result = executorService.ban(id, body.get("reason"), reset);
        operationLogService.log(operator(auth), reset ? "封禁并重置执行器token" : "封禁执行器",
                String.valueOf(id), body.get("reason"), clientIp(request));
        String message = reset ? "已封禁并重置 token，旧 token 立即失效" : "已封禁，该执行器全部请求已被拒绝";
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", message,
                "token", result.get("token")));
    }

    /** 解封（原 token 恢复；若封禁时重置过，需把新 token 配给 agent） */
    @PostMapping("/{id}/unban")
    public ResponseEntity<Map<String, Object>> unban(@PathVariable Long id, Authentication auth,
                                                     HttpServletRequest request) {
        executorService.unban(id);
        operationLogService.log(operator(auth), "解封执行器", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已解封"));
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
