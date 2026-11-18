package com.ripperinio.mobile;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (new BridgeConfig(context).enabled()) {
            try { BridgeService.start(context); } catch (Exception ignored) {}
        }
    }
}
