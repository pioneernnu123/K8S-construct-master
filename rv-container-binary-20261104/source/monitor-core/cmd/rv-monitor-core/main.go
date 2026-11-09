package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"riscv.local/monitorcore/internal/monitor"
)

func main() {
	configPath := flag.String("config.file", "monitor.json", "JSON scrape configuration")
	listen := flag.String("web.listen-address", ":9090", "HTTP listen address")
	flag.Parse()
	if err := run(*configPath, *listen); err != nil {
		log.Fatal(err)
	}
}

func run(path, listen string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(f)
	decoder.DisallowUnknownFields()
	var config monitor.Config
	err = decoder.Decode(&config)
	f.Close()
	if err != nil {
		return fmt.Errorf("configuration: %w", err)
	}
	monitorServer, err := monitor.New(config)
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	server := &http.Server{Addr: listen, Handler: monitorServer.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 30 * time.Second}
	scrapesDone := make(chan struct{})
	go func() { monitorServer.Run(ctx); close(scrapesDone) }()
	go func() {
		<-ctx.Done()
		shutdown, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		server.Shutdown(shutdown)
	}()
	log.Printf("rv-monitor-core: %s, %d targets", listen, len(config.Targets))
	err = server.ListenAndServe()
	cancel()
	<-scrapesDone
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}
