# Traffic monitoring

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Traffic_Monitoring/` |
| Runs on IWR6843AOP | Runs, but the application assumes a roadside mount, a 76-81 GHz part in most variants, and vehicle-scale targets. |
| Prebuilt binaries | `traffic_monitoring_68xx_demo.bin` |
| Needs reflashing | Yes. |

## Relevance

Low. Listed for completeness. The multi-target tracker at range is the same
GTRACK covered under People_Tracking; nothing here is closer to the thesis
than that.
