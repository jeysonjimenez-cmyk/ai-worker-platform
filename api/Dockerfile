FROM golang:1.26-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /api .

FROM alpine:3.21
RUN apk --no-cache add ca-certificates
COPY --from=builder /api /api
EXPOSE 8080
ENTRYPOINT ["/api"]
