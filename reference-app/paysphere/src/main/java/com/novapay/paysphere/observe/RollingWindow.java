package com.novapay.paysphere.observe;

/**
 * The last N outcomes for one (region, journey), which is what a success ratio and a p95 have to be
 * computed over.
 *
 * <p>A lifetime ratio is nearly useless during an incident - a service that has served a million
 * good payments and is now failing every one still reports 0.999 - so the harness reports a rolling
 * window and says how wide it is. The p95 is nearest-rank over exactly the measurements held, never
 * interpolated, so the number reported is a latency that actually happened.
 */
public final class RollingWindow {

    private final boolean[] ok;
    private final double[] latencyMs;
    private final int capacity;
    private int size;
    private int next;

    public RollingWindow(int capacity) {
        this.capacity = Math.max(1, capacity);
        this.ok = new boolean[this.capacity];
        this.latencyMs = new double[this.capacity];
    }

    public synchronized void add(boolean succeeded, double latency) {
        ok[next] = succeeded;
        latencyMs[next] = latency;
        next = (next + 1) % capacity;
        if (size < capacity) {
            size++;
        }
    }

    public synchronized int size() {
        return size;
    }

    /** NaN when nothing has been measured: an unmeasured ratio is not zero. */
    public synchronized double successRatio() {
        if (size == 0) {
            return Double.NaN;
        }
        int good = 0;
        for (int i = 0; i < size; i++) {
            if (ok[i]) {
                good++;
            }
        }
        return (double) good / size;
    }

    public synchronized double percentileMs(double percentile) {
        if (size == 0) {
            return Double.NaN;
        }
        double[] sorted = new double[size];
        System.arraycopy(latencyMs, 0, sorted, 0, size);
        java.util.Arrays.sort(sorted);
        int rank = (int) Math.ceil(percentile * size) - 1;
        return sorted[Math.max(0, Math.min(size - 1, rank))];
    }
}
