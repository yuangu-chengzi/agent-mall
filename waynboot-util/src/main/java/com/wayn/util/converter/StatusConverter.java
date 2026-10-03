package com.wayn.util.converter;

import lombok.Getter;

import java.util.Arrays;
import java.util.List;

/**
 * 通用状态字段转换类
 */
public class StatusConverter extends AbstractIntConverter {

    @Override
    List<ConverterDTO> getArr() {
        StatusEnum[] values = StatusEnum.values();
        return Arrays.stream(values).map(statusEnum -> new ConverterDTO(statusEnum.getType(), statusEnum.getDesc())).toList();
    }

    /**
     * 状态枚举（0=启用，1=禁用）。
     * 说明：成员名原为 MAN/WOMAN（性别枚举复制遗留），与"状态"语义不符，已按语义重命名。
     */
    @Getter
    enum StatusEnum {

        ENABLED(0, "启用"),
        DISABLED(1, "禁用");


        private final Integer type;
        private final String desc;

        StatusEnum(Integer type, String desc) {
            this.type = type;
            this.desc = desc;
        }


    }
}
