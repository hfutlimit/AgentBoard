// SPDX-License-Identifier: MIT
using System.Diagnostics;
using OpenTelemetry;

namespace AgentBoard.Api.Observability;

/// <summary>
/// OpenTelemetry processor that strips sensitive attributes from every span
/// before it is exported. This is defense-in-depth alongside the Serilog
/// header scrubber: even if application code stamps an Authorization / API
/// key / cookie / token as a span attribute, it is removed before reaching
/// any collector — satisfying the #313 masking gate for the OTel surface.
/// </summary>
public sealed class SensitiveAttributeProcessor : BaseProcessor<Activity>
{
    public override void OnStart(Activity data) => Filter(data);

    public override void OnEnd(Activity data) => Filter(data);

    private static void Filter(Activity activity)
    {
        foreach (var key in SensitiveDataScrubber.SensitiveAttributeKeys)
        {
            if (activity.GetTagItem(key) is not null)
            {
                activity.SetTag(key, null);
            }
        }
    }
}
