// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// The OS account the Node process is running as (v4.3 §2.9).
/// </summary>
/// <remarks>
/// <para>
/// v4.3 fix C-5 deliberately names the account identifier
/// <see cref="PrincipalId"/> rather than <c>sid</c>. SID is a Windows-only
/// concept, so an interface carrying that name would leak one platform's model
/// into the cross-platform contract — exactly the kind of drift that later
/// forces every caller into an <c>OperatingSystem.IsWindows()</c> branch. The
/// value is a string because the two platforms disagree on the shape: Windows
/// renders S-1-5-21-... while Unix renders a numeric UID.
/// </para>
/// <para>
/// <see cref="IsElevated"/> exists because the Node must be able to warn when
/// it runs elevated: an elevated process cannot see the user's Provider logins
/// (Codex / CodeBuddy / Cursor keep their OAuth state under the user profile),
/// which is the root cause behind the "LocalSystem breaks every credential"
/// class of failures.
/// </para>
/// </remarks>
public interface IUserIdentity
{
    /// <summary>Account name of the running process (for example "hank").</summary>
    string CurrentUserName { get; }

    /// <summary>
    /// Platform-neutral account identifier: a Windows SID or a Unix UID.
    /// Empty when the platform could not resolve one.
    /// </summary>
    string PrincipalId { get; }

    /// <summary>True when the process runs with administrative / root rights.</summary>
    bool IsElevated { get; }
}
