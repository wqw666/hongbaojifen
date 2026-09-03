package com.hbjf.api;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 红包积分管理 API Server
 * 面向 QQ 群会员的积分管理系统：会员上分/下分/查询/流水 + 玩法规则文件管理
 */
@SpringBootApplication
public class HongbaoApplication {

    public static void main(String[] args) {
        SpringApplication.run(HongbaoApplication.class, args);
    }
}
