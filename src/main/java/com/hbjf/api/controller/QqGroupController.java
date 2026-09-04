package com.hbjf.api.controller;

import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.service.QqGroupService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * QQ群管理（管理员）
 * 状态：active 正常 / banned 封禁（封群=停玩停同步）；封禁可填原因
 */
@RestController
@RequestMapping("/api/admin/qq-groups")
public class QqGroupController {

    private final QqGroupService qqGroupService;
    private final OperationLogService operationLogService;

    public QqGroupController(QqGroupService qqGroupService, OperationLogService operationLogService) {
        this.qqGroupService = qqGroupService;
        this.operationLogService = operationLogService;
    }

    /** 列表：?keyword=&status= */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword,
                                                    @RequestParam(required = false) String status) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", qqGroupService.list(keyword, status)));
    }

    /** 新增 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        qqGroupService.create(body.get("group_id"), body.get("group_name"), body.get("owner_qq"),
                body.get("admin_qqs"), body.get("status"));
        operationLogService.log(operator(auth), "新增QQ群", body.get("group_id"), body.get("group_name"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增"));
    }

    /** 更新 */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                      Authentication auth, HttpServletRequest request) {
        qqGroupService.update(id, body.get("group_name"), body.get("owner_qq"), body.get("admin_qqs"),
                body.get("member_count"), body.get("status"), body.get("note"));
        operationLogService.log(operator(auth), "更新QQ群", String.valueOf(id), body.get("group_name"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 封禁（停玩停同步） */
    @PostMapping("/{id}/ban")
    public ResponseEntity<Map<String, Object>> ban(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                   Authentication auth, HttpServletRequest request) {
        qqGroupService.ban(id, body.get("reason"));
        operationLogService.log(operator(auth), "封禁QQ群", String.valueOf(id), body.get("reason"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已封禁，该群玩法与成员同步将停止"));
    }

    /** 解封 */
    @PostMapping("/{id}/unban")
    public ResponseEntity<Map<String, Object>> unban(@PathVariable Long id, Authentication auth,
                                                     HttpServletRequest request) {
        qqGroupService.unban(id);
        operationLogService.log(operator(auth), "解封QQ群", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已解封"));
    }

    /** 删除 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        qqGroupService.delete(id);
        operationLogService.log(operator(auth), "删除QQ群", String.valueOf(id), "", clientIp(request));
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
