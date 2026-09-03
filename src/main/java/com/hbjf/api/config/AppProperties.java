package com.hbjf.api.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 应用配置属性，对应 application.yml 中的 app.*
 */
@Component
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private final Jwt jwt = new Jwt();
    private final Open open = new Open();
    private String rulesDir = "./data/rules";

    public static class Jwt {
        private String secret = "hbjf_dev_secret_2026";
        private int expireHours = 24;

        public String getSecret() { return secret; }
        public void setSecret(String secret) { this.secret = secret; }
        public int getExpireHours() { return expireHours; }
        public void setExpireHours(int expireHours) { this.expireHours = expireHours; }
    }

    /** 对外开放接口配置（agent 调用） */
    public static class Open {
        private String apiKey = "";

        public String getApiKey() { return apiKey; }
        public void setApiKey(String apiKey) { this.apiKey = apiKey; }
    }

    public Jwt getJwt() { return jwt; }
    public Open getOpen() { return open; }
    public String getRulesDir() { return rulesDir; }
    public void setRulesDir(String rulesDir) { this.rulesDir = rulesDir; }
}
