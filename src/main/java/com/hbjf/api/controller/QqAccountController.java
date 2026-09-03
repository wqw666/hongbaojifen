package com.hbjf.api.controller;

import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.service.QqAccountService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * QQ号管理（type=qq 普通号池 / type=admin_qq 管理员号）
 * 前端两个菜单共用本接口，type 参数区分
 */
@RestController
@RequestMapping("/api/admin/qq-accounts")
public class QqAccountController {

    private final QqAccountService qqAccountService;
    private final OperationLogService operationLogService;

    public QqAccountController(QqAccountService qqAccountService, OperationLogService operationLogService) {
        this.qqAccountService = qqAccountService;
        this.operationLogService = operationLogService;
    }

    /** 列表：?type=qq|admin_qq&qq=过滤 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(defaultValue = "qq") String type,
                                                    @RequestParam(required = false) String qq) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", qqAccountService.list(type, qq)));
    }

    /** 新增单个 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody Map<String, String> body, Authentication auth,
                                                      HttpServletRequest request) {
        qqAccountService.create(body.get("qq"), body.get("type"), body.get("remark"));
        operationLogService.log(operator(auth), "新增QQ号", body.get("qq"), body.get("type"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已新增"));
    }

    /** 批量新增：{type, remark, qq_text} 逗号/换行分隔 */
    @PostMapping("/batch")
    public ResponseEntity<Map<String, Object>> createBatch(@RequestBody Map<String, String> body, Authentication auth,
                                                           HttpServletRequest request) {
        int added = qqAccountService.createBatch(body.get("qq_text"), body.get("type"), body.get("remark"));
        operationLogService.log(operator(auth), "批量新增QQ号", added + "个", body.get("type"), clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "成功新增 " + added + " 个QQ号"));
    }

    /** 删除 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        qqAccountService.delete(id);
        operationLogService.log(operator(auth), "删除QQ号", String.valueOf(id), "", clientIp(request));
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
