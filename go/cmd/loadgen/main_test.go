package main

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestRunCountsRequests(t *testing.T) {
	var served atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if served.Add(1)%10 == 0 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()

	res := run(srv.Client(), http.MethodGet, srv.URL, nil, 4, 200*time.Millisecond)
	if res.Requests == 0 || int64(res.Requests) != served.Load() {
		t.Fatalf("requests %d, served %d", res.Requests, served.Load())
	}
	if res.Non2xx == 0 || res.Errors != 0 {
		t.Fatalf("non2xx %d errors %d", res.Non2xx, res.Errors)
	}
	if res.Latency.P50 > res.Latency.P99 || res.Latency.P99 > res.Latency.Max {
		t.Fatalf("перцентили не упорядочены: %+v", res.Latency)
	}
}

func TestSummarize(t *testing.T) {
	var lat []time.Duration
	for i := 1; i <= 100; i++ {
		lat = append(lat, time.Duration(i)*time.Millisecond)
	}
	l := summarize(lat)
	if l.P50 != 51 || l.P90 != 91 || l.P99 != 100 || l.Max != 100 || l.Mean != 50.5 {
		t.Fatalf("%+v", l)
	}
	if summarize(nil) != (Latency{}) {
		t.Fatal("пустой список")
	}
}
