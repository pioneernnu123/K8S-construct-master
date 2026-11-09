FROM scratch
COPY bin/rv-monitor-core-linux-riscv64 /bin/rv-monitor-core
COPY third_party /licenses/third_party
USER 65534:65534
EXPOSE 9090
ENTRYPOINT ["/bin/rv-monitor-core"]
