package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 执行器心跳（agent 调用，/api/open/** 下）
 * POST /api/open/executor/heartbeat  {token, host?, version?, group_id?}
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
    public ResponseEntity<Map<String, Object>> heartbeat(@RequestBody Map<String, String> body) {
        Map<String, Object> result = executorService.heartbeat(
                body.get("token"), body.get("host"), body.get("version"), body.get("group_id"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", result));
    }
}
