package com.hbjf.api.controller;

import com.hbjf.api.service.GameRecordService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 游戏记录开放接口（agent 上报局结算；需 X-Api-Key）
 *   POST /api/open/games/report
 *     {executor_token, round_id, play_id?, play_name?, group_id?, events:[{qq, nickname?, msg?, reply?, delta}]}
 *   - round_id 幂等：同一局重复上报返回 duplicate，不重复入账
 *   - 未知/停用/余额不足的玩家整条跳过并记 warning，其余照常入账
 *   - 执行器被封禁 / 绑定操作员停用 → 拒绝上报
 */
@RestController
@RequestMapping("/api/open/games")
public class OpenGameController {

    private final GameRecordService gameRecordService;

    public OpenGameController(GameRecordService gameRecordService) {
        this.gameRecordService = gameRecordService;
    }

    /** 上报一局游戏并结算入账 */
    @PostMapping("/report")
    public ResponseEntity<Map<String, Object>> report(@RequestBody Map<String, Object> body) {
        Map<String, Object> result = gameRecordService.reportRound(body);
        boolean duplicate = Boolean.TRUE.equals(result.get("duplicate"));
        return ResponseEntity.ok(MapBuilder.of("code", 0,
                "message", duplicate ? "该局已结算过，未重复入账" : "已记录并结算",
                "data", result));
    }
}
