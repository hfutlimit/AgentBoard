// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity;
using AgentBoard.Application.Identity.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Identity;
using FluentAssertions;
using NSubstitute;

namespace AgentBoard.Application.Tests.Identity;

/// <summary>
/// Provider-layer unit tests (S0-3 acceptance: each layer tested in
/// isolation with the layer below replaced by NSubstitute mocks).
/// IUserService / ITokenService / IClock are mocked, so these tests pin
/// AuthProvider's orchestration rules (credential checks, token issuing,
/// DTO mapping) without touching the Service or any database.
/// </summary>
public sealed class AuthProviderTests
{
    private static readonly DateTime Now = new(2026, 8, 25, 0, 0, 0, DateTimeKind.Utc);

    private readonly IUserService _users = Substitute.For<IUserService>();
    private readonly IClock _clock = Substitute.For<IClock>();
    private readonly ITokenService _tokens = Substitute.For<ITokenService>();

    private AuthProvider CreateSut() => new(_users, _clock, _tokens);

    public AuthProviderTests() => _clock.UtcNow.Returns(Now);

    private static User NewUser(string username = "alice", bool isAdmin = false) =>
        User.Create(username, "stored-hash", isAdmin, Now);

    [Fact]
    public async Task LoginAsync_RejectsBlankCredentials()
    {
        var act = () => CreateSut().LoginAsync("", "secret");

        await act.Should().ThrowAsync<InvalidValueException>();
        await _users.DidNotReceiveWithAnyArgs().GetByUsernameAsync(default!, default);
    }

    [Fact]
    public async Task LoginAsync_ThrowsInvalidValue_WhenUserUnknown()
    {
        _users.GetByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns((User?)null);

        var act = () => CreateSut().LoginAsync("alice", "secret");

        await act.Should().ThrowAsync<InvalidValueException>()
            .WithMessage("invalid credentials*");
    }

    [Fact]
    public async Task LoginAsync_ThrowsInvalidValue_WhenPasswordWrong()
    {
        _users.GetByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns(NewUser());
        _users.VerifyPasswordAsync(0, "wrong", Arg.Any<CancellationToken>()).Returns(false);

        var act = () => CreateSut().LoginAsync("alice", "wrong");

        await act.Should().ThrowAsync<InvalidValueException>()
            .WithMessage("invalid credentials*");
        _tokens.DidNotReceiveWithAnyArgs().IssueToken(default);
    }

    [Fact]
    public async Task LoginAsync_IssuesToken_AndReturnsSession()
    {
        var alice = NewUser();
        _users.GetByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns(alice);
        _users.VerifyPasswordAsync(0, "secret", Arg.Any<CancellationToken>()).Returns(true);
        _tokens.IssueToken(0).Returns("v1.0.9999.sig");

        var session = await CreateSut().LoginAsync("alice", "secret");

        session.Id.Should().Be(alice.Id);
        session.Username.Should().Be("alice");
        session.Token.Should().Be("v1.0.9999.sig");
        session.TokenType.Should().Be("bearer");
    }

    [Fact]
    public async Task GetCurrentAsync_ThrowsNotFound_WhenUserMissing()
    {
        _users.GetByIdAsync(42, Arg.Any<CancellationToken>()).Returns((User?)null);

        var act = () => CreateSut().GetCurrentAsync(42);

        await act.Should().ThrowAsync<NotFoundException>();
    }

    [Fact]
    public async Task GetCurrentAsync_MapsUser_ToDto()
    {
        var alice = NewUser(isAdmin: true);
        alice.UpdateProfile("Alice", "alice@example.com", null, Now, 1);
        _users.GetByIdAsync(0, Arg.Any<CancellationToken>()).Returns(alice);

        var dto = await CreateSut().GetCurrentAsync(0);

        dto.Username.Should().Be("alice");
        dto.DisplayName.Should().Be("Alice");
        dto.Email.Should().Be("alice@example.com");
        dto.IsAdmin.Should().BeTrue();
        dto.CreatedAt.Should().Be(Now);
    }

    [Fact]
    public async Task RegisterAsync_RejectsShortPassword()
    {
        var act = () => CreateSut().RegisterAsync("bob", "short");

        await act.Should().ThrowAsync<InvalidValueException>();
        await _users.DidNotReceiveWithAnyArgs().CreateAsync(default!, default);
    }

    [Fact]
    public async Task RegisterAsync_CreatesUser_AndIssuesToken()
    {
        var created = new UserDto(7, "bob", null, null, null, false, Now, Now);
        _users.CreateAsync(
            Arg.Is<CreateUserRequest>(r => r.Username == "bob" && r.Password == "longenough"),
            Arg.Any<CancellationToken>()).Returns(created);
        _tokens.IssueToken(7).Returns("v1.7.9999.sig");

        var session = await CreateSut().RegisterAsync("bob", "longenough");

        session.Id.Should().Be(7);
        session.Username.Should().Be("bob");
        session.Token.Should().Be("v1.7.9999.sig");
    }

    [Fact]
    public async Task ChangePasswordAsync_Delegates_ToService()
    {
        var sut = CreateSut();

        await sut.ChangePasswordAsync(5, "old", "newlongenough");

        await _users.Received(1).ChangePasswordAsync(
            5, "old", "newlongenough", Arg.Any<CancellationToken>());
    }
}
