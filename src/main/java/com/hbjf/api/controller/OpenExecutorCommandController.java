package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorCommandService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 执行器命令通道开放接口（agent 调用，/api/open/** 下，需 X-Api-Key + 执行器 token）
 *   POST /api/open/executor/commands/poll            {token}             拉取待执行命令（原子标记 sent）
 *   POST /api/open/executor/commands/{id}/result     {token, status, message}  回报 done/failed
 */
@RestController
@RequestMapping("/api/open/executor/commands")
public class OpenExecutorCommandController {

    private final ExecutorCommandService executorCommandService;

    public OpenExecutorCommandController(ExecutorCommandService executorCommandService) {
        this.executorCommandService = executorCommandService;
    }

    /** 拉取命令（sent 超过 5 分钟自动重排回 pending 重新投递） */
    @PostMapping("/poll")
    public ResponseEntity<Map<String, Object>> poll(@RequestBody Map<String, String> body) {
        return ResponseEntity.ok(MapBuilder.of("code", 0,
                "data", MapBuilder.of("commands", executorCommandService.poll(body.get("token")))));
    }

    /** 回报执行结果 */
    @PostMapping("/{id}/result")
    public ResponseEntity<Map<String, Object>> report(@PathVariable Long id, @RequestBody Map<String, String> body) {
        executorCommandService.reportResult(body.get("token"), id, body.get("status"), body.get("message"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已回报"));
    }
}
