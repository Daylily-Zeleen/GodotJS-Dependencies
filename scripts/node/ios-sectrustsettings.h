// iOS shim for the macOS-only SecTrustSettings API used by node's
// crypto_context.cc (system root-certificate trust lookup).
//
// The iOS SDK ships Security.framework with SecTrust.h but NOT
// SecTrustSettings.h (that header is macOS-only, since 10.4), so the
// kSecTrustSettings* keys/domains referenced by node are undeclared on iOS and
// SecTrustSettingsCopyTrustSettings is unavailable. This header is injected
// into the iOS build only (see scripts/node/build-ios.sh) and provides local
// definitions so node compiles. At runtime the stubbed API returns
// errSecItemNotFound, so certificates without explicit trust settings fall
// back to SecTrustEvaluateWithError (IsCertificateTrustValid) - exactly the
// same behavior macOS applies to certs with no explicit trust settings.
#ifndef GODOTJS_IOS_SEC_TRUST_SETTINGS_H
#define GODOTJS_IOS_SEC_TRUST_SETTINGS_H

#include <TargetConditionals.h>
#include <Security/Security.h>

#if defined(__APPLE__) && !TARGET_OS_OSX

// Trust-settings dictionary keys / domains (CFStringRef).
#define kSecTrustSettingsDomainUser CFSTR("kSecTrustSettingsDomainUser")
#define kSecTrustSettingsDomainAdmin CFSTR("kSecTrustSettingsDomainAdmin")
#define kSecTrustSettingsApplication CFSTR("kSecTrustSettingsApplication")
#define kSecTrustSettingsPolicyString CFSTR("kSecTrustSettingsPolicyString")
#define kSecTrustSettingsPolicy CFSTR("kSecTrustSettingsPolicy")
#define kSecTrustSettingsResult CFSTR("kSecTrustSettingsResult")

// Trust-settings result values (CFIndex enum in the macOS SDK).
#define kSecTrustSettingsResultTrustRoot 1
#define kSecTrustSettingsResultTrustAsRoot 2
#define kSecTrustSettingsResultDeny 3

static OSStatus SecTrustSettingsCopyTrustSettings_iOS(
    SecCertificateRef cert, CFStringRef domain, CFArrayRef* trust_settings) {
  (void)cert;
  (void)domain;
  (void)trust_settings;
  // On iOS the SecTrustSettings API is unimplemented; report "no settings" so
  // node falls back to SecTrustEvaluateWithError for the certificate.
  return errSecItemNotFound;
}

#define SecTrustSettingsCopyTrustSettings SecTrustSettingsCopyTrustSettings_iOS

#endif  // __APPLE__ && !TARGET_OS_OSX
#endif  // GODOTJS_IOS_SEC_TRUST_SETTINGS_H
