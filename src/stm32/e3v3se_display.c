// Creality Ender 3 V3 SE stock display serial bridge
//
// Copyright (C) 2026 Bernardo Costa
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memcpy
#include "autoconf.h" // CONFIG_CLOCK_FREQ
#include "basecmd.h" // oid_alloc
#include "board/armcm_boot.h" // armcm_enable_irq
#include "board/io.h" // readb
#include "board/irq.h" // irq_save
#include "command.h" // DECL_COMMAND
#include "internal.h" // enable_pclock
#include "sched.h" // DECL_TASK

#define DISPLAY_BUFFER_SIZE 256
#define DISPLAY_REPORT_SIZE 40
#define DISPLAY_CR1_FLAGS (USART_CR1_UE | USART_CR1_RE | USART_CR1_TE \
                           | USART_CR1_RXNEIE)

static uint8_t receive_buf[DISPLAY_BUFFER_SIZE];
static uint8_t receive_head, receive_tail;
static uint8_t transmit_buf[DISPLAY_BUFFER_SIZE];
static uint8_t transmit_head, transmit_tail;
static uint32_t receive_overflows, transmit_overflows;
static uint8_t display_oid, display_is_configured;
static struct task_wake display_wake;

DECL_CONSTANT("E3V3SE_DISPLAY_BRIDGE", 1);
DECL_CONSTANT("E3V3SE_DISPLAY_MAX_CHUNK", DISPLAY_REPORT_SIZE);
DECL_CONSTANT_STR("RESERVE_PINS_e3v3se_display", "PA3,PA2");

void
USART2_IRQHandler(void)
{
    uint32_t sr = USART2->SR;
    if (sr & (USART_SR_RXNE | USART_SR_ORE)) {
        // Reading SR followed by DR clears RXNE and ORE.
        uint8_t data = USART2->DR;
        uint8_t next = receive_head + 1;
        if (next == readb(&receive_tail)) {
            receive_overflows++;
        } else {
            receive_buf[receive_head] = data;
            writeb(&receive_head, next);
            sched_wake_task(&display_wake);
        }
    }
    if (sr & USART_SR_TXE && USART2->CR1 & USART_CR1_TXEIE) {
        uint8_t tail = transmit_tail;
        if (tail == readb(&transmit_head)) {
            USART2->CR1 = DISPLAY_CR1_FLAGS;
        } else {
            USART2->DR = transmit_buf[tail];
            writeb(&transmit_tail, tail + 1);
        }
    }
}

static void
display_enable_tx_irq(void)
{
    USART2->CR1 = DISPLAY_CR1_FLAGS | USART_CR1_TXEIE;
}

struct e3v3se_display {
    uint8_t oid;
};

void
command_config_e3v3se_display(uint32_t *args)
{
    struct e3v3se_display *display = oid_alloc(
        args[0], command_config_e3v3se_display, sizeof(*display));
    if (display_is_configured)
        shutdown("Only one Ender 3 V3 SE display is supported");
    uint32_t baud = args[1];
    if (!baud)
        shutdown("Invalid Ender 3 V3 SE display baud rate");

    display->oid = display_oid = args[0];
    display_is_configured = 1;
    receive_head = receive_tail = 0;
    transmit_head = transmit_tail = 0;
    receive_overflows = transmit_overflows = 0;

    enable_pclock((uint32_t)USART2);
    uint32_t pclk = get_pclock_frequency((uint32_t)USART2);
    uint32_t div = DIV_ROUND_CLOSEST(pclk, baud);
    USART2->BRR = (((div / 16) << USART_BRR_DIV_Mantissa_Pos)
                   | ((div % 16) << USART_BRR_DIV_Fraction_Pos));
    USART2->CR1 = DISPLAY_CR1_FLAGS;
    armcm_enable_irq(USART2_IRQHandler, USART2_IRQn, 0);
    gpio_peripheral(GPIO('A', 3), GPIO_FUNCTION(7), 1);
    gpio_peripheral(GPIO('A', 2), GPIO_FUNCTION(7), 0);
}
DECL_COMMAND(command_config_e3v3se_display,
             "config_e3v3se_display oid=%c baud=%u");

void
command_e3v3se_display_send(uint32_t *args)
{
    oid_lookup(args[0], command_config_e3v3se_display);
    uint8_t data_len = args[1];
    uint8_t *data = command_decode_ptr(args[2]);
    uint8_t head = readb(&transmit_head);
    uint8_t tail = readb(&transmit_tail);
    uint8_t available = tail - head - 1;
    if (data_len > available) {
        transmit_overflows++;
        return;
    }
    uint8_t pos;
    for (pos = 0; pos < data_len; pos++)
        transmit_buf[head++] = data[pos];
    writeb(&transmit_head, head);
    display_enable_tx_irq();
}
DECL_COMMAND(command_e3v3se_display_send,
             "e3v3se_display_send oid=%c data=%*s");

void
command_e3v3se_display_get_status(uint32_t *args)
{
    oid_lookup(args[0], command_config_e3v3se_display);
    sendf("e3v3se_display_status oid=%c rx_overflows=%u tx_overflows=%u",
          args[0], receive_overflows, transmit_overflows);
}
DECL_COMMAND(command_e3v3se_display_get_status,
             "e3v3se_display_get_status oid=%c");

void
e3v3se_display_task(void)
{
    if (!sched_check_wake(&display_wake) || !display_is_configured)
        return;

    uint8_t data[DISPLAY_REPORT_SIZE];
    irqstatus_t flag = irq_save();
    uint8_t head = readb(&receive_head);
    uint8_t tail = readb(&receive_tail);
    uint8_t count = head - tail;
    if (count > DISPLAY_REPORT_SIZE)
        count = DISPLAY_REPORT_SIZE;
    uint8_t pos;
    for (pos = 0; pos < count; pos++)
        data[pos] = receive_buf[tail++];
    writeb(&receive_tail, tail);
    uint8_t has_more = tail != readb(&receive_head);
    irq_restore(flag);

    if (count)
        sendf("e3v3se_display_response oid=%c data=%*s",
              display_oid, count, data);
    if (has_more)
        sched_wake_task(&display_wake);
}
DECL_TASK(e3v3se_display_task);
