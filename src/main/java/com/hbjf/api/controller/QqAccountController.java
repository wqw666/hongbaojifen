package com.hbjf.api.controller;

import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.service.QqAccountService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 操作员管理（原 QQ号管理：普通号概念已由会员表承载，本接口仅管理员QQ）
 */
@RestController
@RequestMapping("/api/admin/qq-accounts")
public class QqAccountController {

    private final QqAccountService qqAccountService;
    private final OperationLogService operationLogService;

    public QqAccountController(QqAccountService qqAccountService, OperationLogService operationLogService) {
        this.qqAccountService = qqAccountService;
        this.operationLogService = operationLogService;
    }

    /** 操作员列表：?qq=过滤 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String qq) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", qqAccountService.list(qq)));
    }

    /** 新增操作员（手动预登记；正常由 agent 上线自动注册） */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        qqAccountService.create(body.get("qq"), body.get("remark"));
        operationLogService.log(operator(auth), "新增操作员", body.get("qq"), body.get("remark"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增"));
    }

    /** 更新（状态 正常/停用、权限 允许/禁止手动上下分 等） */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                      Authentication auth, HttpServletRequest request) {
        qqAccountService.update(id, body.get("nickname"), body.get("remark"),
                body.get("status"), body.get("can_manual_points"));
        operationLogService.log(operator(auth), "更新操作员", String.valueOf(id),
                "状态=" + body.get("status") + " 权限=" + body.get("can_manual_points"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 删除 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        qqAccountService.delete(id);
        operationLogService.log(operator(auth), "删除操作员", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
