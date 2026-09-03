package com.hbjf.api.util;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Map 构建工具（替代 Java 9+ 的 Map.of，支持任意键值对）
 */
public final class MapBuilder {

    private MapBuilder() {}

    public static Map<String, Object> of(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return m;
    }
}
