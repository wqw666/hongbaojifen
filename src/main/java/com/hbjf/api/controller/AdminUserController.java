package com.hbjf.api.controller;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.service.AdminUserService;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 后台用户管理（仅内置超级管理员 admin 可访问；普通账号调用返回 403）
 * 新增用户 / 列表 / 删除 / 重置密码；普通账号改自己密码走 /api/auth/password
 */
@RestController
@RequestMapping("/api/admin/users")
public class AdminUserController {

    private final AdminUserService adminUserService;
    private final OperationLogService operationLogService;

    public AdminUserController(AdminUserService adminUserService, OperationLogService operationLogService) {
        this.adminUserService = adminUserService;
        this.operationLogService = operationLogService;
    }

    /** 用户列表（仅 admin） */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(Authentication auth) {
        requireBuiltin(auth);
        List<Map<String, Object>> data = adminUserService.list();
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", data));
    }

    /** 新增用户（仅 admin） */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        requireBuiltin(auth);
        adminUserService.create(body.get("username"), body.get("nickname"), body.get("password"));
        operationLogService.log(operator(auth), "新增后台用户", body.get("username"), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增"));
    }

    /** 删除用户（仅 admin；内置 admin 由服务层拒绝） */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        requireBuiltin(auth);
        adminUserService.delete(id);
        operationLogService.log(operator(auth), "删除后台用户", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }

    /** 重置密码（仅 admin，对任意账号含自己） */
    @PutMapping("/{id}/password")
    public ResponseEntity<Map<String, Object>> resetPassword(@PathVariable Long id,
                                                             @RequestBody Map<String, String> body,
                                                             Authentication auth, HttpServletRequest request) {
        requireBuiltin(auth);
        adminUserService.resetPassword(id, body.get("password"));
        operationLogService.log(operator(auth), "重置后台用户密码", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "密码已更新"));
    }

    /** 仅超级管理员可用 */
    private void requireBuiltin(Authentication auth) {
        if (auth == null || !AdminUserService.BUILTIN_ADMIN.equals(auth.getName())) {
            throw new ApiException(ErrorCode.PERMISSION_DENIED, "仅超级管理员可管理用户");
        }
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
