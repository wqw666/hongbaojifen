package com.hbjf.api.controller;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.service.AuthService;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 管理员登录
 */
@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final AuthService authService;
    private final OperationLogService operationLogService;

    public AuthController(AuthService authService, OperationLogService operationLogService) {
        this.authService = authService;
        this.operationLogService = operationLogService;
    }

    /** 登录 */
    @PostMapping("/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody Map<String, String> body, HttpServletRequest request) {
        Map<String, Object> result = authService.login(body.get("username"), body.get("password"));
        operationLogService.log(body.get("username"), "登录", "系统", "管理员登录成功", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", result));
    }

    /** token 校验 */
    @GetMapping("/verify")
    public ResponseEntity<Map<String, Object>> verify(@RequestHeader(value = "Authorization", required = false) String auth) {
        if (auth == null || !auth.startsWith("Bearer ")) {
            throw new ApiException(ErrorCode.NOT_LOGGED_IN, "未登录");
        }
        String username = authService.verify(auth.substring(7));
        if (username == null) {
            throw new ApiException(ErrorCode.TOKEN_EXPIRED, "Token 已过期");
        }
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", MapBuilder.of("username", username)));
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
