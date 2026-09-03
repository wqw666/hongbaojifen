package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorCommandService;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 执行器命令下发（管理员，JWT）
 *   POST /api/admin/executors/{id}/commands   {command, params}  下发（同命令去重）
 *   GET  /api/admin/executors/{id}/commands   ?status=            命令列表（含执行结果）
 */
@RestController
@RequestMapping("/api/admin/executors/{id}/commands")
public class ExecutorCommandController {

    private final ExecutorCommandService executorCommandService;
    private final OperationLogService operationLogService;

    public ExecutorCommandController(ExecutorCommandService executorCommandService, OperationLogService operationLogService) {
        this.executorCommandService = executorCommandService;
        this.operationLogService = operationLogService;
    }

    /** 下发命令 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> dispatch(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                        Authentication auth, HttpServletRequest request) {
        Map<String, Object> result = executorCommandService.dispatch(id, body.get("command"), body.get("params"));
        operationLogService.log(operator(auth), "下发执行器命令", body.get("command"),
                body.get("params") == null ? "" : body.get("params"), clientIp(request));
        String msg = "已下发" + (result.containsKey("message") ? "，" + result.get("message") : "");
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", msg, "data", result));
    }

    /** 命令列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@PathVariable Long id,
                                                    @RequestParam(required = false) String status) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", executorCommandService.listByExecutor(id, status)));
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
