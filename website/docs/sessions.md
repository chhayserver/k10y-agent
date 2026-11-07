# Sessions and account security

## Your account

**My settings** (bottom of the sidebar) is personal:

- **Sidebar pages:** hide pages you don't use. You never see pages you lack permission for.
- **Theme**, **auto-refresh** interval and **start page**.
- **Change password.**
- **Two-factor login** (see below).
- **Signed-in devices:** every browser where you're signed in, with its IP address, when it signed in and when it was last active. **Sign out** one device, or **Sign out all others**.

Sidebar pages, theme, refresh, start page and chart range are saved **to your account**, so they follow you to every device. Table sorting and filters stay in each browser.

<Shot src="/shots/settings.png" alt="My settings with signed-in devices" url="k10y / settings" />

## Passwords

- At least 8 characters, with letters plus numbers or symbols. The change-password page shows a strength meter and the rules as you type.
- Changing your password signs out every other session.
- After 5 failed sign-ins in 5 minutes for the same username and IP, sign-in pauses for a few minutes.

## Two-factor login

Two-factor login adds a 6-digit code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Authy…) to every sign-in.

1. **My settings → Two-factor login → Set up**, and confirm your password.
2. Scan the QR code with the app (or type the key), then enter the code it shows and **Turn on**.
3. Save the **10 recovery codes**: each one signs you in once if you lose your phone. They're shown only once; **New recovery codes** makes a fresh set (the old ones stop working).

At sign-in you enter your password, then the code. A code works only once. **Turn off** needs your password, unless two-factor login is required for you.

**Require it** (needs `settings.manage`): **System settings → Sign-in security → Require two-factor login** for **Admins** (people with a hub-wide permission to manage users, settings, clusters or HAProxy instances) or **Everyone**. Those who haven't set it up are asked to right after signing in, and can't do anything else until they have. You must have it on yourself before you can require it, so you can't lock yourself out.

**Lost phone and no recovery codes?** An admin opens the user in **Administration → Users** and clicks **Reset two-factor**. If it's required, the user sets it up again at the next sign-in. Every enrolment, reset and use of a recovery code is in the activity log.

## For admins

On **Administration → Users** you see each user's **last activity** and number of **active sessions**:

- **Sign out** ends every session of that user.
- **Disable** blocks the account and ends its sessions at once. **Enable** restores it.
- Opening a user shows their signed-in devices and whether they use two-factor login (**Reset two-factor** for a lost phone). The list marks them with **2FA**.

## Sessions

Sessions last 12 hours of inactivity (set `K10Y_SESSION_HOURS` to change it) and extend while you use k10y. The browser cookie is `HttpOnly`, `Secure` and `SameSite=Strict`, and the server only stores a hash of it.
