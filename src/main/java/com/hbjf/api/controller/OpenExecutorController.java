package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 执行器心跳（agent 调用，/api/open/** 下）
 * POST /api/open/executor/heartbeat
 *   {token, host?, version?, admin_qq?, admin_nickname?}   admin_qq=本机登录的管理员QQ（自动注册为操作员）
 * 来源 IP 由服务端从请求侧记录（X-Forwarded-For 或 remoteAddr），agent 无法伪造
 * 调用方需带 X-Api-Key 请求头
 */
@RestController
@RequestMapping("/api/open/executor")
public class OpenExecutorController {

    private final ExecutorService executorService;

    public OpenExecutorController(ExecutorService executorService) {
        this.executorService = executorService;
    }

    /** 心跳注册/续活 */
    @PostMapping("/heartbeat")
    public ResponseEntity<Map<String, Object>> heartbeat(@RequestBody Map<String, String> body,
                                                         HttpServletRequest request) {
        Integer feeRate = null;
        try {
            String fr = body.get("game_fee_rate");
            if (fr != null && !fr.trim().isEmpty()) feeRate = Integer.parseInt(fr.trim());
        } catch (NumberFormatException ignored) {
            // 非法费率忽略，不更新
        }
        Map<String, Object> result = executorService.heartbeat(
                body.get("token"), body.get("host"), body.get("version"),
                body.get("admin_qq"), body.get("admin_nickname"), clientIp(request), feeRate);
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", result));
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
