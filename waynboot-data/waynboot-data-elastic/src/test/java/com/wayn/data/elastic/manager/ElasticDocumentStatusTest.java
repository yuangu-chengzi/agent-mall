package com.wayn.data.elastic.manager;

import org.elasticsearch.rest.RestStatus;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 索引写入结果判定的回归测试。
 * <p>
 * 真实踩过的坑：ES 对**新建**文档返回 201 CREATED、对**覆盖**返回 200 OK，
 * 而原先的判定只认 OK —— 于是每个新商品的同步都会被判成失败（文档其实写成功了），
 * outbox 因此多记一次失败并重试，重试时才因为变成覆盖而通过。
 * 现象是「商品新增总能同步成功，但每条消息都带着一次失败重试」。
 */
class ElasticDocumentStatusTest {

    @Test
    void createdAndOkAreBothSuccess() {
        assertThat(ElasticDocument.isIndexSuccess(RestStatus.CREATED)).isTrue();
        assertThat(ElasticDocument.isIndexSuccess(RestStatus.OK)).isTrue();
    }

    @Test
    void otherStatusesAreNotSuccess() {
        assertThat(ElasticDocument.isIndexSuccess(RestStatus.NOT_FOUND)).isFalse();
        assertThat(ElasticDocument.isIndexSuccess(RestStatus.BAD_REQUEST)).isFalse();
        assertThat(ElasticDocument.isIndexSuccess(RestStatus.CONFLICT)).isFalse();
    }
}
