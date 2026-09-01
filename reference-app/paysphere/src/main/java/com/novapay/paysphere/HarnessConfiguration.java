package com.novapay.paysphere;

import java.time.Clock;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class HarnessConfiguration {

    /**
     * One clock, injected everywhere. The harness has no drill clock of its own: the platform's
     * virtual clock governs the platform's own scenario time, and an application that faked its
     * timestamps to match would make every freshness and ordering claim untestable.
     */
    @Bean
    public Clock clock() {
        return Clock.systemUTC();
    }
}
