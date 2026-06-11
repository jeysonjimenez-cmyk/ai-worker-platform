package ssrf

import (
	"fmt"
	"net"
	"net/url"
)

// blockedCIDRs covers RFC 1918, loopback, link-local, and Tailscale (100.64.0.0/10).
var blockedCIDRs []*net.IPNet

func init() {
	for _, cidr := range []string{
		"10.0.0.0/8",
		"172.16.0.0/12",
		"192.168.0.0/16",
		"127.0.0.0/8",
		"::1/128",
		"169.254.0.0/16",
		"100.64.0.0/10",
	} {
		_, block, _ := net.ParseCIDR(cidr)
		blockedCIDRs = append(blockedCIDRs, block)
	}
}

// Validate checks that u is a safe https:// URL (no private/Tailscale IPs).
// It resolves DNS to catch late-binding SSRF.
func Validate(rawURL string) error {
	if rawURL == "" {
		return nil
	}
	u, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid url: %w", err)
	}
	if u.Scheme != "https" {
		return fmt.Errorf("webhook_url must use https")
	}
	host := u.Hostname()
	addrs, err := net.LookupHost(host)
	if err != nil {
		return fmt.Errorf("cannot resolve host %q: %w", host, err)
	}
	for _, addr := range addrs {
		ip := net.ParseIP(addr)
		if ip == nil {
			continue
		}
		for _, block := range blockedCIDRs {
			if block.Contains(ip) {
				return fmt.Errorf("webhook_url resolves to blocked IP %s", ip)
			}
		}
	}
	return nil
}

// ValidateStatic checks URL without resolving DNS (for unit tests with synthetic URLs).
func ValidateStatic(rawURL string) error {
	if rawURL == "" {
		return nil
	}
	u, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid url: %w", err)
	}
	if u.Scheme != "https" {
		return fmt.Errorf("webhook_url must use https")
	}
	host := u.Hostname()
	if ip := net.ParseIP(host); ip != nil {
		for _, block := range blockedCIDRs {
			if block.Contains(ip) {
				return fmt.Errorf("webhook_url points to blocked IP %s", ip)
			}
		}
	}
	return nil
}
