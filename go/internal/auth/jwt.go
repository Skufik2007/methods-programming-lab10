// Package auth выпускает и проверяет JWT (RS256) и публикует открытый ключ в формате JWKS.
//
// Асимметричная подпись выбрана намеренно: Python-сервис проверяет токены по
// открытому ключу из /.well-known/jwks.json и не знает секрета, которым их можно
// подделать. Подписывать токены может только Go-сервис.
package auth

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// Config — параметры выпускаемых токенов.
type Config struct {
	Issuer   string
	Audience string
	TTL      time.Duration
}

// Claims — полезная нагрузка токена.
type Claims struct {
	Role string `json:"role"`
	jwt.RegisteredClaims
}

// Issuer подписывает и проверяет токены одним RSA-ключом.
type Issuer struct {
	cfg    Config
	key    *rsa.PrivateKey
	kid    string
	parser *jwt.Parser
}

func NewIssuer(cfg Config, key *rsa.PrivateKey) (*Issuer, error) {
	if cfg.Issuer == "" || cfg.Audience == "" || cfg.TTL <= 0 {
		return nil, errors.New("auth: issuer, audience и TTL обязательны")
	}
	kid, err := keyID(&key.PublicKey)
	if err != nil {
		return nil, err
	}
	return &Issuer{
		cfg: cfg,
		key: key,
		kid: kid,
		parser: jwt.NewParser(
			jwt.WithValidMethods([]string{jwt.SigningMethodRS256.Alg()}), // защита от alg=none и подмены на HS256
			jwt.WithIssuer(cfg.Issuer),
			jwt.WithAudience(cfg.Audience),
			jwt.WithExpirationRequired(),
			jwt.WithIssuedAt(),
			jwt.WithLeeway(30*time.Second), // допуск на расхождение часов между сервисами
		),
	}, nil
}

// Issue выпускает токен для пользователя.
func (i *Issuer) Issue(subject, role string) (string, time.Time, error) {
	now := time.Now()
	exp := now.Add(i.cfg.TTL)
	claims := Claims{
		Role: role,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    i.cfg.Issuer,
			Subject:   subject,
			Audience:  jwt.ClaimStrings{i.cfg.Audience},
			IssuedAt:  jwt.NewNumericDate(now),
			NotBefore: jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(exp),
			ID:        uuid.NewString(),
		},
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = i.kid
	signed, err := tok.SignedString(i.key)
	return signed, exp, err
}

// Verify проверяет подпись, алгоритм, iss, aud и сроки действия.
func (i *Issuer) Verify(raw string) (*Claims, error) {
	claims := &Claims{}
	_, err := i.parser.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		if kid, _ := t.Header["kid"].(string); kid != i.kid {
			return nil, fmt.Errorf("неизвестный kid %q", kid)
		}
		return &i.key.PublicKey, nil
	})
	if err != nil {
		return nil, err
	}
	if claims.Subject == "" {
		return nil, errors.New("в токене нет sub")
	}
	return claims, nil
}

// JWK — открытый RSA-ключ в формате RFC 7517.
type JWK struct {
	Kty string `json:"kty"`
	Use string `json:"use"`
	Alg string `json:"alg"`
	Kid string `json:"kid"`
	N   string `json:"n"`
	E   string `json:"e"`
}

type JWKS struct {
	Keys []JWK `json:"keys"`
}

func (i *Issuer) JWKS() JWKS {
	pub := i.key.PublicKey
	return JWKS{Keys: []JWK{{
		Kty: "RSA",
		Use: "sig",
		Alg: jwt.SigningMethodRS256.Alg(),
		Kid: i.kid,
		N:   b64(pub.N.Bytes()),
		E:   b64(big.NewInt(int64(pub.E)).Bytes()),
	}}}
}

// keyID — первые 16 символов base64url(SHA-256(DER открытого ключа)).
// Детерминирован: один и тот же ключ после перезапуска даёт тот же kid.
func keyID(pub *rsa.PublicKey) (string, error) {
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(der)
	return b64(sum[:])[:16], nil
}

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

// LoadOrGenerateKey читает RSA-ключ из PEM (PKCS#1 или PKCS#8). Если path пустой,
// генерирует временный ключ: удобно для разработки, но токены не переживут
// перезапуск, поэтому в docker-compose ключ передаётся файлом.
func LoadOrGenerateKey(path string) (*rsa.PrivateKey, error) {
	if path == "" {
		return rsa.GenerateKey(rand.Reader, 2048)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("чтение ключа: %w", err)
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, errors.New("файл ключа не в формате PEM")
	}
	if k, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		return k, nil
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("разбор ключа: %w", err)
	}
	k, ok := parsed.(*rsa.PrivateKey)
	if !ok {
		return nil, errors.New("ключ не RSA")
	}
	return k, nil
}
