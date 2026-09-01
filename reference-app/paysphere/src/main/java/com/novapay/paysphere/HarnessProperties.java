package com.novapay.paysphere;

import java.util.ArrayList;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Everything the harness lets an operator move without editing code. */
@ConfigurationProperties(prefix = "paysphere")
public class HarnessProperties {

    private Keys keys = new Keys();
    private List<String> regions = new ArrayList<>(List.of("region-a", "region-b"));
    private String logFile = "./logs/observe.jsonl";
    private int logRingSize = 5000;
    private int traceRingSize = 2000;
    private Trafficgen trafficgen = new Trafficgen();

    public Keys getKeys() {
        return keys;
    }

    public void setKeys(Keys keys) {
        this.keys = keys;
    }

    public List<String> getRegions() {
        return regions;
    }

    public void setRegions(List<String> regions) {
        this.regions = regions;
    }

    public String getLogFile() {
        return logFile;
    }

    public void setLogFile(String logFile) {
        this.logFile = logFile;
    }

    public int getLogRingSize() {
        return logRingSize;
    }

    public void setLogRingSize(int logRingSize) {
        this.logRingSize = logRingSize;
    }

    public int getTraceRingSize() {
        return traceRingSize;
    }

    public void setTraceRingSize(int traceRingSize) {
        this.traceRingSize = traceRingSize;
    }

    public Trafficgen getTrafficgen() {
        return trafficgen;
    }

    public void setTrafficgen(Trafficgen trafficgen) {
        this.trafficgen = trafficgen;
    }

    /**
     * Three credentials, not one. The platform binds a read-only connector for telemetry, a
     * remediation connector for actions and a drill connector for chaos; giving them one shared
     * key would make the credential-scope floor unprovable from this side.
     */
    public static class Keys {

        private String readOnly = "";
        private String remediation = "";
        private String drill = "";
        private String deploy = "";

        public String getDeploy() {
            return deploy;
        }

        public void setDeploy(String deploy) {
            this.deploy = deploy;
        }

        public String getReadOnly() {
            return readOnly;
        }

        public void setReadOnly(String readOnly) {
            this.readOnly = readOnly;
        }

        public String getRemediation() {
            return remediation;
        }

        public void setRemediation(String remediation) {
            this.remediation = remediation;
        }

        public String getDrill() {
            return drill;
        }

        public void setDrill(String drill) {
            this.drill = drill;
        }
    }

    public static class Trafficgen {

        private boolean enabled = true;
        private long intervalMs = 1000L;
        private int paymentsPerTick = 4;
        private long seed = 20260901L;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public long getIntervalMs() {
            return intervalMs;
        }

        public void setIntervalMs(long intervalMs) {
            this.intervalMs = intervalMs;
        }

        public int getPaymentsPerTick() {
            return paymentsPerTick;
        }

        public void setPaymentsPerTick(int paymentsPerTick) {
            this.paymentsPerTick = paymentsPerTick;
        }

        public long getSeed() {
            return seed;
        }

        public void setSeed(long seed) {
            this.seed = seed;
        }
    }
}
