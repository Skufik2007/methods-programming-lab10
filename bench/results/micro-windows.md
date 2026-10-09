# Микробенчмарки валидации (Windows 11, 12 логических ядер)

## Go: go test -bench . -benchmem

```
goos: windows
cpu: AMD Ryzen 5 5600H with Radeon Graphics         
BenchmarkValidate-12    	  268626	      4766 ns/op	     316 B/op	      11 allocs/op
goos: windows
cpu: AMD Ryzen 5 5600H with Radeon Graphics         
BenchmarkPing-12             	   71196	     14188 ns/op	    7244 B/op	      41 allocs/op
BenchmarkValidateOrder-12    	   45979	     25853 ns/op	    9630 B/op	      77 allocs/op
```

## Python: bench/micro_validate.py (Pydantic)

```
model_validate(dict)          12.23 мкс/операция   пик памяти   2400 Б/операция
model_validate_json(str)      12.12 мкс/операция   пик памяти   2408 Б/операция
```
