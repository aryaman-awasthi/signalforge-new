package com.novapay.paysphere.actions;

import java.util.Map;

/** One executable action. The returned map becomes the execution's {@code output}. */
@FunctionalInterface
public interface ActionHandler {

    Map<String, Object> run(Map<String, Object> params) throws Exception;
}
