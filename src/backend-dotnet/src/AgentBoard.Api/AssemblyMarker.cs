// SPDX-License-Identifier: MIT
namespace AgentBoard.Api;

/// <summary>Marker class so NetArchTest can locate the API assembly without
/// referencing a Controller (which would pull in MVC and fail on missing
/// hosting configuration).</summary>
public static class AssemblyMarker;
