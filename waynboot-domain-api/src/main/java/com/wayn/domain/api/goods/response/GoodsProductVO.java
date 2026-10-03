package com.wayn.domain.api.goods.response;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.io.Serial;
import java.io.Serializable;
import java.math.BigDecimal;

@Data
@EqualsAndHashCode(callSuper = false)
public class GoodsProductVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1472445142327045417L;
    private Long id;

    /**
     * 商品表的商品ID
     */
    private Long goodsId;

    /**
     * 商品规格值列表，采用JSON数组格式
     */
    @TableField(typeHandler = JacksonTypeHandler.class)
    @Size(min = 1, message = "商品规格值列表不能小于1")
    private String[] specifications;

    /**
     * 商品货品价格
     */
    @DecimalMin(value = "0.0001", message = "货品售价不能小于0")
    private BigDecimal price;

    /**
     * 是否默认选中（0代表选中 1代表未选中）
     */
    private Boolean defaultSelected;

    /**
     * 商品货品数量
     */
    @Min(value = 0, message = "商品货品数量不能小于0")
    private Integer number;

    /**
     * 乐观锁版本号：管理端编辑商品时须回传页面读到的版本号，用于防止"陈旧表单"覆盖并发库存变更。
     * 不传（旧前端）时退化为历史行为（不做并发校验）。
     */
    private Integer version;


}
