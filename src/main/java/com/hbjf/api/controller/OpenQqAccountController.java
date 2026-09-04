package com.hbjf.api.controller;

import com.hbjf.api.service.OpenQqAccountService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 操作员开放接口（agent 维护其登录的管理员QQ；调用方需带 X-Api-Key）
 *   POST   /api/open/qq-accounts    {qq, nickname?, remark?}  新增或更新（幂等；恒为 admin_qq）
 *   GET    /api/open/qq-accounts                               操作员列表（含权限/登录信息）
 *   DELETE /api/open/qq-accounts/{qq}                          删除
 * 常规自注册走心跳（admin_qq 字段），本接口供 agent GUI 手动维护
 */
@RestController
@RequestMapping("/api/open/qq-accounts")
public class OpenQqAccountController {

    private final OpenQqAccountService openQqAccountService;

    public OpenQqAccountController(OpenQqAccountService openQqAccountService) {
        this.openQqAccountService = openQqAccountService;
    }

    /** 上报/更新操作员 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> upsert(@RequestBody Map<String, String> body) {
        Map<String, Object> result = openQqAccountService.upsert(
                body.get("qq"), body.get("nickname"), body.get("remark"));
        boolean created = Boolean.TRUE.equals(result.get("created"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", created ? "操作员已新增" : "操作员已更新", "data", result));
    }

    /** 操作员列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list() {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", openQqAccountService.list()));
    }

    /** 删除 */
    @DeleteMapping("/{qq}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable String qq) {
        openQqAccountService.deleteByQq(qq);
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }
}
