package com.novapay.paysphere;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * PaySphere: the reference payments application SignalForge onboards.
 *
 * <p>It is a harness, not a product. Its whole job is to be an honest example of an application
 * that has never heard of SignalForge and is onboarded anyway: it serves the frozen
 * {@code /observe/*} contract, publishes an action manifest and a chaos manifest against the
 * frozen JSON schemas, and its deployment pipeline posts to the frozen ingest contract. There is
 * no SignalForge SDK here, no callback registration and no shared library - the only coupling in
 * either direction is the wire format.
 */
@SpringBootApplication
@EnableScheduling
@EnableConfigurationProperties(HarnessProperties.class)
public class PaysphereApplication {

    public static void main(String[] args) {
        SpringApplication.run(PaysphereApplication.class, args);
    }
}
