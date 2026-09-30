/*
* Copyright (C) 2024 Texas Instruments Incorporated
*
* All rights reserved not granted herein.
* Limited License.  
*
* Texas Instruments Incorporated grants a world-wide, royalty-free, 
* non-exclusive license under copyrights and patents it now or hereafter 
* owns or controls to make, have made, use, import, offer to sell and sell ("Utilize")
* this software subject to the terms herein.  With respect to the foregoing patent 
* license, such license is granted  solely to the extent that any such patent is necessary 
* to Utilize the software alone.  The patent license shall not apply to any combinations which 
* include this software, other than combinations with devices manufactured by or for TI ("TI Devices").  
* No hardware patent is licensed hereunder.
*
* Redistributions must preserve existing copyright notices and reproduce this license (including the 
* above copyright notice and the disclaimer and (if applicable) source code license limitations below) 
* in the documentation and/or other materials provided with the distribution
*
* Redistribution and use in binary form, without modification, are permitted provided that the following
* conditions are met:
*
*	* No reverse engineering, decompilation, or disassembly of this software is permitted with respect to any 
*     software provided in binary form.
*	* any redistribution and use are licensed by TI for use only with TI Devices.
*	* Nothing shall obligate TI to provide you with source code for the software licensed and provided to you in object code.
*
* If software source code is provided to you, modification and redistribution of the source code are permitted 
* provided that the following conditions are met:
*
*   * any redistribution and use of the source code, including any resulting derivative works, are licensed by 
*     TI for use only with TI Devices.
*   * any redistribution and use of any object code compiled from the source code and any resulting derivative 
*     works, are licensed by TI for use only with TI Devices.
*
* Neither the name of Texas Instruments Incorporated nor the names of its suppliers may be used to endorse or 
* promote products derived from this software without specific prior written permission.
*
* DISCLAIMER.
*
* THIS SOFTWARE IS PROVIDED BY TI AND TI'S LICENSORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, 
* BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
* IN NO EVENT SHALL TI AND TI'S LICENSORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR 
* CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, 
* OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
* OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE 
* POSSIBILITY OF SUCH DAMAGE.
*/

#ifndef MMW_OUTPUT_H
#define MMW_OUTPUT_H

#ifdef __cplusplus
extern "C"
{
#endif

/** @brief Output packet length is a multiple of this value, must be power of 2*/
#define MMWDEMO_OUTPUT_MSG_SEGMENT_LEN 32

    /*!
     * @brief
     *  Message types used in Millimeter Wave Demo for the communication between
     *  target and host, and also for Mailbox communication
     *  between MSS and DSS on the XWR63xx platform. Message types are used to indicate
     *  different type detection information sent out from the target.
     *
     */
    typedef enum MmwDemo_output_message_type_e
    {
        /*! @brief   List of detected points */
        MMWDEMO_OUTPUT_MSG_DETECTED_POINTS = 1,

        /*! @brief   Range profile */
        MMWDEMO_OUTPUT_MSG_RANGE_PROFILE,

        /*! @brief   Noise floor profile */
        MMWDEMO_OUTPUT_MSG_NOISE_PROFILE,

        /*! @brief   Samples to calculate static azimuth  heatmap */
        MMWDEMO_OUTPUT_MSG_AZIMUT_STATIC_HEAT_MAP,

        /*! @brief   Range/Doppler detection matrix */
        MMWDEMO_OUTPUT_MSG_RANGE_DOPPLER_HEAT_MAP,

        /*! @brief   Stats information */
        MMWDEMO_OUTPUT_MSG_STATS,

        /*! @brief   List of detected points */
        MMWDEMO_OUTPUT_MSG_DETECTED_POINTS_SIDE_INFO,

        /*! @brief   Feature vectors that are calculated from detection matrix and 2D FFT Output */
        MMWDEMO_OUTPUT_MSG_GESTURE_FEATURES = 1050,

        /*! @brief   Output probability from Neural Network */
        MMWDEMO_OUTPUT_MSG_ANN_OP_PROB,

        MMWDEMO_OUTPUT_MSG_MAX
    } MmwDemo_output_message_type;

    /*!
     * @brief
     *  Message header for reporting detection information from data path.
     *
     * @details
     *  The structure defines the message header.
     */
    typedef struct MmwDemo_output_message_header_t
    {
        /*! @brief   Output buffer magic word (sync word). It is initialized to  {0x0102,0x0304,0x0506,0x0708} */
        uint16_t magicWord[4];

        /*! brief   Version: : MajorNum * 2^24 + MinorNum * 2^16 + BugfixNum * 2^8 + BuildNum   */
        uint32_t version;

        /*! @brief   Total packet length including header in Bytes */
        uint32_t totalPacketLen;

        /*! @brief   platform type */
        uint32_t platform;

        /*! @brief   Frame number */
        uint32_t frameNumber;

        /*! @brief   Time in CPU cycles when the message was created. For XWR16xx/XWR18xx: DSP CPU cycles, for XWR14xx: R4F CPU cycles */
        uint32_t timeCpuCycles;

        /*! @brief   Number of detected objects */
        uint32_t numDetectedObj;

        /*! @brief   Number of TLVs */
        uint32_t numTLVs;

        /*! @brief   For Advanced Frame config, this is the sub-frame number in the range
         * 0 to (number of subframes - 1). For frame config (not advanced), this is always
         * set to 0. */
        uint32_t subFrameNumber;
    } MmwDemo_output_message_header;

    /*!
     * @brief
     * Structure holds message stats information from data path.
     *
     * @details
     *  The structure holds stats information. This is a payload of the TLV message item
     *  that holds stats information.
     */
    typedef struct MmwDemo_output_message_stats_t
    {
        /*! @brief   Interframe processing time in usec */
        uint32_t interFrameProcessingTime;

        /*! @brief   Transmission time of output detection information in usec */
        uint32_t transmitOutputTime;

        /*! @brief   Interframe processing margin in usec */
        uint32_t interFrameProcessingMargin;

        /*! @brief   Interchirp processing margin in usec */
        uint32_t interChirpProcessingMargin;

        /*! @brief   CPU Load (%) during active frame duration */
        uint32_t activeFrameCPULoad;

        /*! @brief   CPU Load (%) during inter frame duration */
        uint32_t interFrameCPULoad;

    } MmwDemo_output_message_stats;

    /**
     * @brief
     *  Message for reporting detected objects from data path.
     *
     * @details
     *  The structure defines the message body for detected objects from from data path.
     */
    typedef struct MmwDemo_output_message_tl_t
    {
        /*! @brief   TLV type */
        uint32_t type;

        /*! @brief   Length in bytes */
        uint32_t length;

    } MmwDemo_output_message_tl;


#ifdef __cplusplus
}
#endif

#endif /* MMW_OUTPUT_H */
