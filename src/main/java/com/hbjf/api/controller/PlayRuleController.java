package com.hbjf.api.controller;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.service.PlayRuleService;
import com.hbjf.api.util.MapBuilder;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * 会员玩法管理（管理员）：上传规则文件/脚本，启用停用
 */
@RestController
@RequestMapping("/api/admin/rules")
public class PlayRuleController {

    private final PlayRuleService playRuleService;
    private final OperationLogService operationLogService;

    public PlayRuleController(PlayRuleService playRuleService, OperationLogService operationLogService) {
        this.playRuleService = playRuleService;
        this.operationLogService = operationLogService;
    }

    /** 列表：?keyword= */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", playRuleService.list(keyword)));
    }

    /** 上传玩法文件（multipart: file + name + description + version） */
    @PostMapping
    public ResponseEntity<Map<String, Object>> upload(@RequestParam("file") MultipartFile file,
                                                      @RequestParam("name") String name,
                                                      @RequestParam(required = false) String description,
                                                      @RequestParam(required = false) String version,
                                                      Authentication auth, HttpServletRequest request) {
        Map<String, Object> result = playRuleService.upload(name, description, version, file);
        operationLogService.log(operator(auth), "上传玩法", name, "大小 " + result.get("file_size") + "B", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "上传成功", "data", result));
    }

    /** 更新元数据 */
    @PutMapping("/{id}")
    public ResponseEntity<Map<String, Object>> update(@PathVariable Long id, @RequestBody Map<String, String> body,
                                                      Authentication auth, HttpServletRequest request) {
        playRuleService.update(id, body.get("name"), body.get("description"), body.get("version"), body.get("status"));
        operationLogService.log(operator(auth), "更新玩法", body.get("name"), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已更新"));
    }

    /** 删除（含磁盘文件） */
    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> delete(@PathVariable Long id, Authentication auth,
                                                      HttpServletRequest request) {
        playRuleService.delete(id);
        operationLogService.log(operator(auth), "删除玩法", String.valueOf(id), "", clientIp(request));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "已删除"));
    }

    /** 下载文件（管理端预览/备份） */
    @GetMapping("/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable Long id) {
        Map<String, Object> row = playRuleService.findById(id);
        if (row == null) throw new ApiException(ErrorCode.NOT_FOUND, "玩法不存在");
        File f = playRuleService.getFile(row);
        String encoded = URLEncoder.encode(String.valueOf(row.get("name")), StandardCharsets.UTF_8).replace("+", "%20");
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .body(new FileSystemResource(f));
    }

    private String operator(Authentication auth) {
        return auth == null ? "admin" : auth.getName();
    }

    private String clientIp(HttpServletRequest request) {
        String ip = request.getHeader("X-Forwarded-For");
        return (ip != null && !ip.isEmpty()) ? ip.split(",")[0].trim() : request.getRemoteAddr();
    }
}
