package com.novapay.paysphere.trafficgen;

import java.util.List;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

import com.novapay.paysphere.HarnessProperties;
import com.novapay.paysphere.payments.Journey;
import com.novapay.paysphere.payments.PaymentService;

import jakarta.annotation.PreDestroy;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * A steady, seeded stream of real payments across both regions.
 *
 * <p>Without it the harness is a set of endpoints that always return zero, and every downstream
 * claim - baselines, anomaly detection, burn rates, "region-b is worse than region-a" - has nothing
 * to be computed from. The generator is seeded so a demo replays comparably, and the journey mix is
 * weighted so the two tier-1 journeys carry most of the volume, like a real payments business.
 *
 * <p>It submits to a small pool rather than placing payments inline, because a latency fault that
 * really sleeps would otherwise back the scheduler up and quietly stop the traffic at exactly the
 * moment the traffic matters most.
 */
@Component
@ConditionalOnProperty(prefix = "paysphere.trafficgen", name = "enabled", havingValue = "true",
        matchIfMissing = true)
public class TrafficGenerator {

    private static final Logger LOG = LoggerFactory.getLogger(TrafficGenerator.class);

    /** Weighted so upi and card dominate, as they do in the business being modelled. */
    private static final Journey[] MIX = {
        Journey.UPI, Journey.UPI, Journey.UPI, Journey.UPI,
        Journey.CARD, Journey.CARD, Journey.CARD,
        Journey.WALLET, Journey.WALLET,
        Journey.REFUND
    };

    private final PaymentService payments;
    private final List<String> regions;
    private final int perTick;
    private final Random random;
    private final ExecutorService pool = Executors.newFixedThreadPool(8, runnable -> {
        Thread thread = new Thread(runnable, "trafficgen");
        thread.setDaemon(true);
        return thread;
    });

    public TrafficGenerator(PaymentService payments, HarnessProperties properties) {
        this.payments = payments;
        this.regions = List.copyOf(properties.getRegions());
        this.perTick = Math.max(1, properties.getTrafficgen().getPaymentsPerTick());
        this.random = new Random(properties.getTrafficgen().getSeed() + 17);
        LOG.info(
                "traffic generator on: {} payments every {}ms across {}",
                perTick,
                properties.getTrafficgen().getIntervalMs(),
                regions);
    }

    @Scheduled(fixedDelayString = "${paysphere.trafficgen.interval-ms:1000}")
    public void tick() {
        for (int i = 0; i < perTick; i++) {
            String region = regions.get(random.nextInt(regions.size()));
            Journey journey = MIX[random.nextInt(MIX.length)];
            long amountMinor = 5_000L + random.nextInt(495_000);
            try {
                pool.submit(() -> place(journey, region, amountMinor));
            } catch (RejectedExecutionException shuttingDown) {
                return;
            }
        }
    }

    private void place(Journey journey, String region, long amountMinor) {
        try {
            payments.place(journey, region, amountMinor, false);
        } catch (RuntimeException exc) {
            // Generated traffic must never take the harness down with it: a failing payment is the
            // point, and a failing generator is only noise.
            LOG.debug("generated payment failed", exc);
        }
    }

    @PreDestroy
    void stop() {
        pool.shutdownNow();
    }
}
