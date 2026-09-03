package com.hbjf.api.service;

import com.hbjf.api.config.AppProperties;
import com.hbjf.api.dao.RowMapMapper;
import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 会员玩法管理 — 玩法规则文件（游戏规则/脚本）存磁盘 data/rules/，表里存元数据
 * agent（执行器）通过开放接口拉取启用的玩法文件，引导群里会员玩游戏
 */
@Service
public class PlayRuleService {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final JdbcTemplate jdbc;
    private final AppProperties props;

    public PlayRuleService(JdbcTemplate jdbc, AppProperties props) {
        this.jdbc = jdbc;
        this.props = props;
    }

    private File rulesDir() {
        File dir = new File(props.getRulesDir());
        if (!dir.exists() && !dir.mkdirs()) {
            throw new ApiException(ErrorCode.INTERNAL_ERROR, "无法创建玩法文件目录: " + dir.getAbsolutePath());
        }
        return dir;
    }

    /** 上传玩法文件 */
    public Map<String, Object> upload(String name, String description, String version, MultipartFile file) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "玩法名称不能为空");
        if (file == null || file.isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "文件不能为空");
        if (file.getSize() > 20L * 1024 * 1024) throw new ApiException(ErrorCode.PARAM_INVALID, "文件不能超过20MB");

        String original = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();
        String ext = "";
        int dot = original.lastIndexOf('.');
        if (dot >= 0) ext = original.substring(dot).toLowerCase();
        String diskName = UUID.randomUUID().toString().replace("-", "") + ext;
        File target = new File(rulesDir(), diskName);
        try {
            file.transferTo(target);
        } catch (IOException e) {
            throw new ApiException(ErrorCode.INTERNAL_ERROR, "文件保存失败: " + e.getMessage());
        }

        String now = LocalDateTime.now().format(FMT);
        try {
            jdbc.update("INSERT INTO play_rule_files (name, description, file_name, file_size, version, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    name.trim(), description == null ? "" : description, diskName, file.getSize(),
                    version == null || version.isEmpty() ? "1.0" : version, "active", now, now);
        } catch (Exception e) {
            target.delete(); // 入库失败清理磁盘文件
            throw e;
        }
        return MapBuilder.of("name", name.trim(), "file_size", file.getSize());
    }

    /** 列表 */
    public List<Map<String, Object>> list(String keyword) {
        if (keyword != null && !keyword.isEmpty()) {
            return jdbc.query(
                    "SELECT id, name, description, file_size, version, status, created_at, updated_at FROM play_rule_files WHERE name LIKE ? ORDER BY id DESC",
                    new RowMapMapper(), "%" + keyword + "%");
        }
        return jdbc.query(
                "SELECT id, name, description, file_size, version, status, created_at, updated_at FROM play_rule_files ORDER BY id DESC",
                new RowMapMapper());
    }

    /** 更新元数据 */
    public void update(Long id, String name, String description, String version, String status) {
        if (name == null || name.trim().isEmpty()) throw new ApiException(ErrorCode.PARAM_INVALID, "玩法名称不能为空");
        String now = LocalDateTime.now().format(FMT);
        jdbc.update("UPDATE play_rule_files SET name=?, description=?, version=?, status=?, updated_at=? WHERE id=?",
                name.trim(), description == null ? "" : description,
                version == null || version.isEmpty() ? "1.0" : version,
                status == null ? "active" : status, now, id);
    }

    /** 删除（含磁盘文件） */
    public void delete(Long id) {
        Map<String, Object> row = findById(id);
        if (row == null) throw new ApiException(ErrorCode.NOT_FOUND, "玩法不存在");
        jdbc.update("DELETE FROM play_rule_files WHERE id=?", id);
        File f = new File(rulesDir(), String.valueOf(row.get("file_name")));
        if (f.exists()) f.delete();
    }

    /** 按 id 查（含磁盘文件名） */
    public Map<String, Object> findById(Long id) {
        try {
            return jdbc.queryForObject(
                    "SELECT id, name, description, file_name, file_size, version, status, created_at, updated_at FROM play_rule_files WHERE id=?",
                    new RowMapMapper(), id);
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            return null;
        }
    }

    /** agent 拉取：启用的玩法列表（不含磁盘文件名） */
    public List<Map<String, Object>> listActiveForAgent() {
        return jdbc.query(
                "SELECT id, name, description, file_size, version, status, updated_at FROM play_rule_files WHERE status='active' ORDER BY id DESC",
                new RowMapMapper());
    }

    /** 读取玩法文件内容（agent 下载用） */
    public File getFile(Map<String, Object> row) {
        File f = new File(rulesDir(), String.valueOf(row.get("file_name")));
        if (!f.exists()) throw new ApiException(ErrorCode.NOT_FOUND, "玩法文件已丢失");
        return f;
    }
}
