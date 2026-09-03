package com.hbjf.api.controller;

import com.hbjf.api.service.OpenQqAccountService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * QQ号开放接口（agent 维护 QQ 列表；调用方需带 X-Api-Key）
 *   POST   /api/open/qq-accounts    {qq, type?, nickname?, remark?}  新增或更新（幂等；type=qq/admin_qq）
 *   GET    /api/open/qq-accounts    ?type=qq|admin_qq                列表
 *   DELETE /api/open/qq-accounts/{qq}                                删除
 */
@RestController
@RequestMapping("/api/open/qq-accounts")
public class OpenQqAccountController {

    private final OpenQqAccountService openQqAccountService;

    public OpenQqAccountController(OpenQqAccountService openQqAccountService) {
        this.openQqAccountService = openQqAccountService;
    }

    /** 上报/更新 QQ 号 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> upsert(@RequestBody Map<String, String> body) {
        Map<String, Object> result = openQqAccountService.upsert(
                body.get("qq"), body.get("type"), body.get("nickname"), body.get("remark"));
        boolean created = Boolean.TRUE.equals(result.get("created"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", created ? "QQ号已新增" : "QQ号已更新", "data", result));
    }

    /** 列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String type) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", openQqAccountService.list(type)));
    }

    /** 删除 */
    @DeleteMapping("/{qq}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable String qq) {
        openQqAccountService.deleteByQq(qq);
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }
}
