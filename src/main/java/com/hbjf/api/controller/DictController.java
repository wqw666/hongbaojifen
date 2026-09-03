package com.hbjf.api.controller;

import com.hbjf.api.service.DictService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 字典管理：通用键值配置的增删改查
 */
@RestController
@RequestMapping("/api/admin/dicts")
public class DictController {

    private final DictService dictService;

    public DictController(DictService dictService) {
        this.dictService = dictService;
    }

    /** 列表：?key=xx 模糊匹配 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String key) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", dictService.list(key)));
    }

    /** 新增 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body) {
        dictService.create(body.get("key"), body.get("value"), body.get("description"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增"));
    }

    /** 更新 */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body) {
        dictService.update(id, body.get("key"), body.get("value"), body.get("description"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 删除 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id) {
        dictService.delete(id);
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }
}
